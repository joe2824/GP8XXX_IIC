"""
Store sequence of the GP8XXX DACs

The sequence isn't valid I2C (3 bit frame, bytes the chip doesn't acknowledge),
so a hardware I2C controller can't send it. It is bit-banged on the SDA/SCL pins.
"""
import re
import shutil
import subprocess
import time
from contextlib import contextmanager

STORE_TIMING_HEAD = 0x02
STORE_TIMING_ADDR = 0x10
STORE_TIMING_CMD1 = 0x03
STORE_TIMING_CMD2 = 0x00
# Datasheet requires at least 7ms
STORE_TIMING_DELAY = 0.01

# pinctrl output, e.g. " 2: a3    pu | hi // GPIO2 = SDA1" or " 17: op dl pd | lo // ..."
_PINCTRL_GET = re.compile(r'^\s*(\d+):\s*(a\d+|ip|op|no)\s+(?:(dh|dl)\s+)?(pu|pd|pn)\s*\|')


class LgpioPins:
    """
    Open-drain GPIO pins via lgpio (works on Raspberry Pi 5 and older models).
    Level 0 pulls the line low, level 1 releases it to the pull-up resistor.
    """

    def __init__(self, *pins):
        import lgpio  # pylint: disable=import-outside-toplevel
        self._lgpio = lgpio
        self._pins = []
        self._handle = self._open_pinctrl_chip()
        try:
            for pin in pins:
                lgpio.gpio_claim_output(self._handle, pin, 1, lgpio.SET_OPEN_DRAIN)
                self._pins.append(pin)
        except Exception:
            self.close()
            raise

    def _open_pinctrl_chip(self):
        # The header pins are gpiochip4 on older Pi 5 kernels, gpiochip0 otherwise
        last_error = None
        for chip in range(16):
            try:
                handle = self._lgpio.gpiochip_open(chip)
            except self._lgpio.error as error:
                last_error = error
                continue
            try:
                if self._lgpio.gpio_get_chip_info(handle)[3].startswith('pinctrl-'):
                    return handle
            except self._lgpio.error as error:
                last_error = error
            self._lgpio.gpiochip_close(handle)
        raise RuntimeError(
            "No pinctrl GPIO chip found. Check that the user is in the 'gpio' group "
            "and the working directory is writable (lgpio creates files there).") from last_error

    def set(self, pin, level):
        self._lgpio.gpio_write(self._handle, pin, level)

    def close(self):
        for pin in self._pins:
            self._lgpio.gpio_free(self._handle, pin)
        self._lgpio.gpiochip_close(self._handle)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class BitBangI2C:
    """
    Minimal bit-banged I2C master.
    - param pins: open-drain pin driver with set(pin, level)
    - param sda_pin: BCM number of the SDA pin
    - param scl_pin: BCM number of the SCL pin
    """
    # Half clock period in seconds, the real delay of time.sleep is longer
    _DELAY = 5e-6

    def __init__(self, pins, sda_pin, scl_pin):
        self._pins = pins
        self._sda = sda_pin
        self._scl = scl_pin

    def _set(self, pin, level):
        self._pins.set(pin, level)
        time.sleep(self._DELAY)

    def recover(self):
        """
        Release a device that was interrupted in the middle of a transfer and
        still holds SDA low: 9 clocks with SDA released, then STOP.
        """
        self._set(self._sda, 1)
        for _ in range(9):
            self._set(self._scl, 0)
            self._set(self._scl, 1)
        self._set(self._scl, 0)
        self.stop()

    def start(self):
        self._set(self._sda, 1)
        self._set(self._scl, 1)
        self._set(self._sda, 0)
        self._set(self._scl, 0)

    def stop(self):
        self._set(self._sda, 0)
        self._set(self._scl, 1)
        self._set(self._sda, 1)

    def send_byte(self, data, bits=8, ack=True):
        """
        Send the lowest `bits` bits of data MSB first. With ack=False no
        acknowledge clock follows and SCL is left high (as in the datasheet).
        """
        for i in range(bits - 1, -1, -1):
            self._set(self._sda, (data >> i) & 1)
            self._set(self._scl, 1)
            self._set(self._scl, 0)
        if not ack:
            self._set(self._sda, 0)
            self._set(self._scl, 1)
            return
        # Release SDA for the acknowledge bit. The chip doesn't acknowledge every
        # byte of the store sequence, so the value is clocked but ignored.
        self._set(self._sda, 1)
        self._set(self._scl, 1)
        self._set(self._scl, 0)


def send_store_sequence(bus, device_addr):
    """
    Send the store sequence of the datasheet on a bit-banged bus
    """
    bus.start()
    bus.send_byte(STORE_TIMING_HEAD, bits=3, ack=False)
    bus.stop()
    bus.start()
    bus.send_byte(STORE_TIMING_ADDR)
    bus.send_byte(STORE_TIMING_CMD1)
    bus.stop()

    bus.start()
    bus.send_byte(device_addr << 1)
    for _ in range(8):
        bus.send_byte(STORE_TIMING_CMD2)
    bus.stop()

    time.sleep(STORE_TIMING_DELAY)

    bus.start()
    bus.send_byte(STORE_TIMING_HEAD, bits=3, ack=False)
    bus.stop()
    bus.start()
    bus.send_byte(STORE_TIMING_ADDR)
    bus.send_byte(STORE_TIMING_CMD2)
    bus.stop()


def _get_pin_function(pin):
    """
    Read the function, drive and pull of a pin with pinctrl, e.g. ['a3', 'pu']
    """
    output = subprocess.run(['pinctrl', 'get', str(pin)], check=True,
                            capture_output=True, text=True).stdout
    match = _PINCTRL_GET.match(output)
    if not match or int(match.group(1)) != pin:
        raise RuntimeError(f"Unexpected pinctrl output for GPIO{pin}: {output.strip()!r}")
    return [token for token in match.groups()[1:] if token]


def _set_pin_functions(functions):
    """
    Restore all pins, even if restoring one of them fails
    """
    errors = []
    for pin, function in functions.items():
        try:
            subprocess.run(['pinctrl', 'set', str(pin), *function], check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            errors.append(f"GPIO{pin}: {error}")
    if errors:
        raise RuntimeError(
            "Couldn't restore the I2C pins, hardware I2C may not work until reboot: "
            + "; ".join(errors))


@contextmanager
def saved_pin_functions(*pins):
    """
    Save the pin functions (e.g. I2C) and restore them afterwards. Fails before
    touching the pins if they can't be read.
    """
    if not shutil.which('pinctrl'):
        raise RuntimeError(
            "pinctrl not found, the I2C pins couldn't be restored after store(). "
            "Install it with 'sudo apt install raspi-utils'.")
    functions = {pin: _get_pin_function(pin) for pin in pins}
    try:
        yield
    finally:
        _set_pin_functions(functions)
