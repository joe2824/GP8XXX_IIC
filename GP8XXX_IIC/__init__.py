import shutil
import subprocess
import time
from abc import ABC
from smbus2 import SMBus

# i2c address
GP8XXX_I2C_DEVICE_ADDR = 0x58


class _LgpioPins:
    """
    Open-drain GPIO pins via lgpio (works on Raspberry Pi 5 and older models).
    Level 0 pulls the line low, level 1 releases it to the pull-up resistor.
    """

    def __init__(self, pins):
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
        for chip in range(16):
            try:
                handle = self._lgpio.gpiochip_open(chip)
            except self._lgpio.error:
                continue
            if self._lgpio.gpio_get_chip_info(handle)[3].startswith('pinctrl-'):
                return handle
            self._lgpio.gpiochip_close(handle)
        raise RuntimeError("No pinctrl GPIO chip found.")

    def set(self, pin, level):
        self._lgpio.gpio_write(self._handle, pin, level)

    def close(self):
        for pin in self._pins:
            self._lgpio.gpio_free(self._handle, pin)
        self._lgpio.gpiochip_close(self._handle)


class _BitBangI2C:
    """
    Minimal bit-banged I2C master, needed for the store sequence.
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


def _get_pin_functions(pins):
    """
    Read the current function and pull of each pin with pinctrl (e.g. 'a3 pu'
    for I2C1 on the Pi 5, 'a0 pu' on older models).
    """
    if not shutil.which('pinctrl'):
        raise RuntimeError(
            "pinctrl not found, the I2C pins couldn't be restored after store(). "
            "Install it with 'sudo apt install raspi-utils'.")
    functions = {}
    for pin in pins:
        # Output looks like: " 2: a3    pu | hi // GPIO2 = SDA1"
        output = subprocess.run(['pinctrl', 'get', str(pin)], check=True,
                                capture_output=True, text=True).stdout
        functions[pin] = output.split(':', 1)[1].split('|')[0].split()
    return functions


def _set_pin_functions(functions):
    for pin, function in functions.items():
        subprocess.run(['pinctrl', 'set', str(pin), *function], check=True)


class GP8XXX(ABC):
    # Select DAC output voltage of 0-5V
    OUTPUT_RANGE_2_5V = 0
    # Select DAC output voltage of 0-5V
    OUTPUT_RANGE_5V = 1
    # Select DAC output voltage of 0-10V
    OUTPUT_RANGE_10V = 2
    # Select DAC output voltage of 0-VCC
    OUTPUT_RANGE_VCC = 3
    RESOLUTION_12_BIT = 0x0FFF
    RESOLUTION_15_BIT = 0x7FFF

    def __init__(self):
        pass

    def begin(self):
        pass

    def set_dac_out_voltage(self, voltage, channel):
        pass


class GP8XXX_IIC(GP8XXX):
    """
    I2C class initialization
    - param resolution: the resolution of the chip
    - param bus: the i2c bus number
    - param device_addr: the I2C device address
    - param auto_range: automatically selects the correct output range
    """
    GP8XXX_CONFIG_REG = 0x02
    GP8XXX_STORE_TIMING_HEAD = 0x02
    GP8XXX_STORE_TIMING_ADDR = 0x10
    GP8XXX_STORE_TIMING_CMD1 = 0x03
    GP8XXX_STORE_TIMING_CMD2 = 0x00
    # Datasheet requires at least 7ms
    GP8XXX_STORE_TIMING_DELAY = 0.01

    # Number of output channels
    CHANNELS = 2
    # Whether the output range (0-5V/0-10V) is set by register
    HAS_RANGE_REGISTER = True

    def __init__(self, resolution, bus=1, device_addr=GP8XXX_I2C_DEVICE_ADDR, auto_range=True):
        self._resolution = resolution
        self._bus = bus
        self._device_addr = device_addr
        self._auto_range = auto_range and self.HAS_RANGE_REGISTER
        # The DAC code is left-aligned in the 16 bit data word
        self._data_shift = 4 if resolution == self.RESOLUTION_12_BIT else 1
        self.channel0 = {'value': 0, 'changed': False, 'dac_voltage': None}
        self.channel1 = {'value': 0, 'changed': False, 'dac_voltage': None}

        self._i2c = SMBus(self._bus)

        self._dac_voltage = None

    def begin(self):
        """
        Check if the DAC acknowledges its address, returns True for success
        """
        try:
            self._i2c.write_quick(self._device_addr)
        except OSError:
            return False
        return True

    def set_dac_outrange(self, output_range: int = GP8XXX.OUTPUT_RANGE_10V):
        """
        Set the DAC output range
        - param output_range [int]: OUTPUT_RANGE_5V or OUTPUT_RANGE_10V
        """
        if not self.HAS_RANGE_REGISTER:
            raise ValueError("DAC doesn't support another output range.")

        if output_range == self.OUTPUT_RANGE_5V:
            self._dac_voltage = 5000
            self._i2c.write_byte_data(
                self._device_addr, self.GP8XXX_CONFIG_REG >> 1, 0x00)
        elif output_range == self.OUTPUT_RANGE_10V:
            self._dac_voltage = 10000
            self._i2c.write_byte_data(
                self._device_addr, self.GP8XXX_CONFIG_REG >> 1, 0x11)
        else:
            raise ValueError("Unsupported output range. Use OUTPUT_RANGE_5V or OUTPUT_RANGE_10V.")

    def _check_channel(self, channel):
        if self.CHANNELS == 1 and channel != 0:
            raise ValueError(
                "Unsupported channel. The DAC only supports channel 0.")
        if channel not in (0, 1, 2):
            raise ValueError(
                "Unsupported channel. Use 0, 1 or 2 (all channels).")

    def _write_channel(self, channel, code):
        """
        Write a DAC code to a channel, low byte first
        """
        register = self.GP8XXX_CONFIG_REG << channel
        self._i2c.write_word_data(
            self._device_addr, register, code << self._data_shift)

    def set_dac_out_voltage(self, voltage: float, channel: int = 0):
        """
        Set different channel output DAC values
        - param voltage [float]: output voltage in volts (e.g. 4.321)
        - param channel [int]: integer representing the output channel
          - 0: Channel 0
          - 1: Channel 1
          - 2: All channels
        """
        self._check_channel(channel)

        # Clamp to 0V and the highest voltage the DAC can output
        max_voltage = 10000 if self.HAS_RANGE_REGISTER else self._dac_voltage
        voltage = min(max(float(voltage) * 1000, 0), max_voltage)

        channels = [self.channel0, self.channel1] if channel == 2 else [
            (self.channel0, self.channel1)[channel]]
        for state in channels:
            state['changed'] = state['changed'] or state['value'] != voltage
            state['value'] = voltage

        # Select the output range, the range applies to both channels
        if self._auto_range:
            output_range = self.OUTPUT_RANGE_5V
            if max(self.channel0['value'], self.channel1['value']) > 5000:
                output_range = self.OUTPUT_RANGE_10V
            if self._dac_voltage != (5000 if output_range == self.OUTPUT_RANGE_5V else 10000):
                self.set_dac_outrange(output_range)

        if self._dac_voltage is None:
            raise RuntimeError(
                "Output range not set. Call set_dac_outrange() or enable auto_range.")

        # A range change also changes the code of the other channel
        for index, state in enumerate((self.channel0, self.channel1)[:self.CHANNELS]):
            if state['dac_voltage'] == self._dac_voltage and not state['changed']:
                continue
            value = min(state['value'], self._dac_voltage)
            code = round(value / self._dac_voltage * self._resolution)
            self._write_channel(index, code)
            state['dac_voltage'] = self._dac_voltage
            state['changed'] = False

    def store(self, sda_pin: int = 2, scl_pin: int = 3, pins=None):
        """
        Save the current output in the chip, so it is restored after a power cycle.

        The store sequence contains a 3 bit frame and bytes the chip doesn't
        acknowledge. A hardware I2C controller (smbus) can't send that, so the
        sequence is bit-banged on the SDA/SCL pins (takes about 15ms). Other
        programs using the same bus during that time get I2C errors.
        - param sda_pin [int]: BCM number of the SDA pin (default: GPIO2)
        - param scl_pin [int]: BCM number of the SCL pin (default: GPIO3)
        - param pins: open-drain pin driver with set(pin, level) and close().
          If omitted, lgpio is used and the pin functions are saved with
          pinctrl beforehand and restored afterwards, so I2C keeps working.
        """
        functions = None
        if pins is None:
            functions = _get_pin_functions((sda_pin, scl_pin))

        try:
            if pins is None:
                pins = _LgpioPins((sda_pin, scl_pin))
            try:
                self._send_store_sequence(_BitBangI2C(pins, sda_pin, scl_pin))
            finally:
                pins.close()
        finally:
            if functions is not None:
                _set_pin_functions(functions)

    def _send_store_sequence(self, bus):
        bus.start()
        bus.send_byte(self.GP8XXX_STORE_TIMING_HEAD, bits=3, ack=False)
        bus.stop()
        bus.start()
        bus.send_byte(self.GP8XXX_STORE_TIMING_ADDR)
        bus.send_byte(self.GP8XXX_STORE_TIMING_CMD1)
        bus.stop()

        bus.start()
        bus.send_byte(self._device_addr << 1)
        for _ in range(8):
            bus.send_byte(self.GP8XXX_STORE_TIMING_CMD2)
        bus.stop()

        time.sleep(self.GP8XXX_STORE_TIMING_DELAY)

        bus.start()
        bus.send_byte(self.GP8XXX_STORE_TIMING_HEAD, bits=3, ack=False)
        bus.stop()
        bus.start()
        bus.send_byte(self.GP8XXX_STORE_TIMING_ADDR)
        bus.send_byte(self.GP8XXX_STORE_TIMING_CMD2)
        bus.stop()


class GP8503(GP8XXX_IIC):
    """
    12bit DAC Dual Channel I2C to 0-2.5V/0-VCC
    - param bus: the i2c bus number
    - param vref: full scale voltage, 2.5V with REFEN at VCC (switch 0-2.5V),
      the supply voltage VCC with REFEN at GND (switch 0-VCC)
    """
    HAS_RANGE_REGISTER = False

    def __init__(self, bus=1, vref=2.5):
        super().__init__(bus=bus, resolution=self.RESOLUTION_12_BIT, auto_range=False)
        self._dac_voltage = vref * 1000


class GP8211S(GP8XXX_IIC):
    """
    15 bit DAC I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param auto_range: automatically selects the correct output range
    """
    # The datasheet documents range values 0x55/0x77 with right-aligned data,
    # this driver uses 0x00/0x11 with left-aligned data like DFRobot
    CHANNELS = 1

    def __init__(self, bus=1, auto_range=True):
        super().__init__(bus=bus, resolution=self.RESOLUTION_15_BIT, auto_range=auto_range)


class GP8512(GP8XXX_IIC):
    """
    15bit DAC I2C to 0-2.5V/0-VCC
    - param bus: the i2c bus number
    - param vref: full scale voltage, 2.5V with REFEN at VCC (switch 0-2.5V),
      the supply voltage VCC with REFEN at GND (switch 0-VCC)
    """
    CHANNELS = 1
    HAS_RANGE_REGISTER = False

    def __init__(self, bus=1, vref=2.5):
        super().__init__(bus=bus, resolution=self.RESOLUTION_15_BIT, auto_range=False)
        # Unlike the other 15 bit DACs the data is right-aligned
        self._data_shift = 0
        self._dac_voltage = vref * 1000


class GP8413(GP8XXX_IIC):
    """
    15bit DAC Dual Channel I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param auto_range: automatically selects the correct output range
    """

    def __init__(self, bus=1, i2c_addr=0x58, auto_range=True):
        super().__init__(bus=bus, resolution=self.RESOLUTION_15_BIT,
                         device_addr=i2c_addr, auto_range=auto_range)
        self.set_dac_outrange(self.OUTPUT_RANGE_10V)


class GP8403(GP8XXX_IIC):
    """
    12bit DAC Dual Channel I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param auto_range: automatically selects the correct output range
    """

    def __init__(self, bus=1, i2c_addr=0x58, auto_range=True):
        super().__init__(bus=bus, resolution=self.RESOLUTION_12_BIT,
                         device_addr=i2c_addr, auto_range=auto_range)


class GP8302(GP8XXX_IIC):
    """
    12bit DAC I2C to 0-25mA
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param full_scale_ma: output current at DAC code 0xFFF, 50V / Rset
      (25mA with Rset = 2kOhm, 20mA with Rset = 2.5kOhm)
    """
    CHANNELS = 1
    HAS_RANGE_REGISTER = False

    def __init__(self, bus=1, i2c_addr=0x58, full_scale_ma=25):
        super().__init__(bus=bus, resolution=self.RESOLUTION_12_BIT,
                         device_addr=i2c_addr, auto_range=False)
        self._full_scale_ma = full_scale_ma
        self._dac_4 = 0
        self._dac_20 = 0
        self._calibration = False

    def calibration4_20ma(self, dac_4=655, dac_20=3277):
        """
        Calibrate the current within 4-20mA
        - param dac_4: Range 0-0xFFF, the DAC value corresponding to current of 4mA generally fluctuates at about 655, the actual value needs to be tested by the user in actual applications
        - param dac_20: Range 0-0xFFF, the DAC value corresponding to current of 20mA generally fluctuates at about 3277, the actual value needs to be tested by the user in actual applications
        """
        if not 0 <= dac_4 < dac_20 <= self._resolution:
            raise ValueError("Invalid calibration, 0 <= dac_4 < dac_20 <= 0xFFF required.")
        self._dac_4 = dac_4
        self._dac_20 = dac_20
        self._calibration = True

    def set_dac_out_voltage(self, voltage: float, channel: int = 0):
        raise NotImplementedError(
            "GP8302 has a current output, use set_dac_out_electric_current().")

    def set_dac_out_electric_current(self, current: float):
        """
        Set the output current, calibrated within 4-20mA after calibration4_20ma()
        - param current [float]: output current in mA (e.g. 12.5)
        - return [int]: the DAC code written
        """
        current = min(max(float(current), 0), self._full_scale_ma)
        if self._calibration and 4 <= current <= 20:
            code = round(self._dac_4 + (current - 4) * (self._dac_20 - self._dac_4) / 16)
        else:
            code = round(current / self._full_scale_ma * self._resolution)
        self._write_channel(0, code)
        return code
