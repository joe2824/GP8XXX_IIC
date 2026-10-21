import warnings
from abc import ABC
from contextlib import ExitStack
from smbus2 import SMBus

from .store import BitBangI2C, LgpioPins, saved_pin_functions, send_store_sequence

# i2c address
GP8XXX_I2C_DEVICE_ADDR = 0x58


class GP8XXX(ABC):
    # Select DAC output voltage of 0-2.5V
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
    # Number of output channels
    CHANNELS = 2

    def __init__(self, resolution, bus=1, device_addr=GP8XXX_I2C_DEVICE_ADDR, auto_range=True):
        self._resolution = resolution
        self._bus = bus
        self._device_addr = device_addr
        self._auto_range = auto_range
        # The DAC code is left-aligned in the 16 bit data word
        self._data_shift = 4 if resolution == self.RESOLUTION_12_BIT else 1

        self._i2c = SMBus(self._bus)

    def begin(self):
        """
        Check if the DAC acknowledges its address
        - return [int]: 0 for success, 1 for failure
        """
        try:
            self._i2c.write_quick(self._device_addr)
        except OSError:
            return 1
        return 0

    def _channels(self, channel):
        """
        Indexes of the channels selected by channel (2: all channels)
        """
        if channel == 2:
            return list(range(self.CHANNELS))
        if channel in range(self.CHANNELS):
            return [channel]
        if channel == 1:
            raise ValueError(
                "Unsupported channel. The DAC only supports channel 0.")
        warnings.warn(f"Unsupported channel {channel} ignored, use 0, 1 or 2.")
        return []

    def _write_code(self, channel, code):
        """
        Write a DAC code to a channel, low byte first
        """
        self._i2c.write_word_data(
            self._device_addr, self.GP8XXX_CONFIG_REG << channel, code << self._data_shift)

    def store(self, sda_pin: int = None, scl_pin: int = None, pins=None):
        """
        Save the current output in the chip, so it is restored after a power cycle.

        The store sequence can't be sent by the I2C controller, so it is bit-banged
        on the SDA/SCL pins (takes about 15ms). Other programs using the same bus
        during that time get I2C errors.
        - param sda_pin [int]: BCM number of the SDA pin, default GPIO2 on bus 1
        - param scl_pin [int]: BCM number of the SCL pin, default GPIO3 on bus 1
        - param pins: open-drain pin driver with set(pin, level). If omitted, lgpio
          is used and the pin functions are restored with pinctrl afterwards.
        """
        if sda_pin is None and scl_pin is None and self._bus in (1, '/dev/i2c-1'):
            sda_pin, scl_pin = 2, 3
        if sda_pin is None or scl_pin is None:
            raise ValueError(
                "Pass sda_pin and scl_pin of the I2C bus the DAC is connected to.")

        with ExitStack() as stack:
            if pins is None:
                stack.enter_context(saved_pin_functions(sda_pin, scl_pin))
                pins = stack.enter_context(LgpioPins(sda_pin, scl_pin))
            bus = BitBangI2C(pins, sda_pin, scl_pin)
            bus.recover()
            send_store_sequence(bus, self._device_addr)


class _VoltageDAC(GP8XXX_IIC):
    """
    Voltage output with a fixed full scale
    - param full_scale: full scale voltage in mV
    """

    def __init__(self, resolution, full_scale, **kwargs):
        super().__init__(resolution, **kwargs)
        self._dac_voltage = full_scale
        # Requested voltage in mV and written DAC code per channel, None until set
        self._voltages = [None, None]
        self._codes = [None, None]

    def _channel_state(self, channel):
        return {
            'value': self._voltages[channel] or 0,
            'changed': False,
            'dac_voltage': None if self._codes[channel] is None else self._dac_voltage,
        }

    @property
    def channel0(self):
        return self._channel_state(0)

    @property
    def channel1(self):
        return self._channel_state(1)

    def set_dac_outrange(self, output_range: int = GP8XXX.OUTPUT_RANGE_10V):
        raise ValueError("DAC doesn't support another output range.")

    def set_dac_out_voltage(self, voltage: float, channel: int = 0):
        """
        Set different channel output DAC values
        - param voltage [float]: output voltage in volts (e.g. 4.321), clamped to the output range
        - param channel [int]: integer representing the output channel
          - 0: Channel 0
          - 1: Channel 1
          - 2: All channels
        """
        channels = self._channels(channel)
        if not channels:
            return
        voltage = max(float(voltage) * 1000, 0)
        for index in channels:
            self._voltages[index] = voltage
        self._apply(self._full_scale_for_voltages())

    def _full_scale_for_voltages(self):
        return self._dac_voltage

    def _write_full_scale(self, full_scale):
        self._dac_voltage = full_scale

    def _apply(self, full_scale):
        """
        Write the full scale and the codes of all set channels. During a range
        change only lower voltages occur: with a higher range the codes are
        written first, with a lower (or unknown) range the range is written first.
        """
        self._voltages = [None if v is None else min(v, full_scale) for v in self._voltages]
        codes = [None if v is None else round(v / full_scale * self._resolution)
                 for v in self._voltages]

        if self._dac_voltage is None or full_scale < self._dac_voltage:
            self._write_full_scale(full_scale)
            self._write_codes(codes)
        else:
            self._write_codes(codes)
            self._write_full_scale(full_scale)

    def _write_codes(self, codes):
        for channel, code in enumerate(codes):
            if code is not None and code != self._codes[channel]:
                self._write_code(channel, code)
                self._codes[channel] = code


class _RangedDAC(_VoltageDAC):
    """
    Voltage output with 0-5V/0-10V range register
    """
    # Output range: (full scale in mV, register value)
    _RANGES = {
        GP8XXX.OUTPUT_RANGE_5V: (5000, 0x00),
        GP8XXX.OUTPUT_RANGE_10V: (10000, 0x11),
    }

    def __init__(self, resolution, **kwargs):
        super().__init__(resolution, full_scale=None, **kwargs)

    def set_dac_outrange(self, output_range: int = GP8XXX.OUTPUT_RANGE_10V):
        """
        Set the DAC output range, set channels keep their voltage (clamped to the range)
        - param output_range [int]: OUTPUT_RANGE_5V or OUTPUT_RANGE_10V
        """
        if output_range not in self._RANGES:
            warnings.warn(
                "Unsupported output range ignored, use OUTPUT_RANGE_5V or OUTPUT_RANGE_10V.")
            return
        self._apply(self._RANGES[output_range][0])

    def _full_scale_for_voltages(self):
        if not self._auto_range:
            if self._dac_voltage is None:
                raise RuntimeError(
                    "Output range not set. Call set_dac_outrange() or enable auto_range.")
            return self._dac_voltage
        highest = max((v for v in self._voltages if v is not None), default=0)
        return 10000 if highest > 5000 else 5000

    def _write_full_scale(self, full_scale):
        if full_scale == self._dac_voltage:
            return
        register_value = next(value for scale, value in self._RANGES.values()
                              if scale == full_scale)
        self._i2c.write_byte_data(
            self._device_addr, self.GP8XXX_CONFIG_REG >> 1, register_value)
        self._dac_voltage = full_scale


class GP8503(_VoltageDAC):
    """
    12bit DAC Dual Channel I2C to 0-2.5V/0-VCC
    - param bus: the i2c bus number
    - param vref: full scale voltage, 2.5V with REFEN at VCC (switch 0-2.5V),
      the supply voltage VCC with REFEN at GND (switch 0-VCC)
    """

    def __init__(self, bus=1, vref=2.5):
        super().__init__(self.RESOLUTION_12_BIT, full_scale=vref * 1000,
                         bus=bus, auto_range=False)


class GP8512(_VoltageDAC):
    """
    15bit DAC I2C to 0-2.5V/0-VCC
    - param bus: the i2c bus number
    - param vref: full scale voltage, 2.5V with REFEN at VCC (switch 0-2.5V),
      the supply voltage VCC with REFEN at GND (switch 0-VCC)
    """
    CHANNELS = 1

    def __init__(self, bus=1, vref=2.5):
        super().__init__(self.RESOLUTION_15_BIT, full_scale=vref * 1000,
                         bus=bus, auto_range=False)
        # Unlike the other 15 bit DACs the data is right-aligned
        self._data_shift = 0


class GP8211S(_RangedDAC):
    """
    15 bit DAC I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param auto_range: automatically selects the correct output range
    """
    # The datasheet documents range values 0x55/0x77 with right-aligned data,
    # this driver uses 0x00/0x11 with left-aligned data like DFRobot
    CHANNELS = 1

    def __init__(self, bus=1, auto_range=True):
        super().__init__(self.RESOLUTION_15_BIT, bus=bus, auto_range=auto_range)


class GP8413(_RangedDAC):
    """
    15bit DAC Dual Channel I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param auto_range: automatically selects the correct output range
    """

    def __init__(self, bus=1, i2c_addr=0x58, auto_range=True):
        super().__init__(self.RESOLUTION_15_BIT, bus=bus,
                         device_addr=i2c_addr, auto_range=auto_range)
        self.set_dac_outrange(self.OUTPUT_RANGE_10V)


class GP8403(_RangedDAC):
    """
    12bit DAC Dual Channel I2C to 0-5V/0-10V
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param auto_range: automatically selects the correct output range
    """

    def __init__(self, bus=1, i2c_addr=0x58, auto_range=True):
        super().__init__(self.RESOLUTION_12_BIT, bus=bus,
                         device_addr=i2c_addr, auto_range=auto_range)


class GP8302(GP8XXX_IIC):
    """
    12bit DAC I2C to 0-25mA
    - param bus: the i2c bus number
    - param i2c_addr: the I2C device address
    - param full_scale: output current in A at DAC code 0xFFF, 50V / Rset
      (0.025A with Rset = 2kOhm, 0.020A with Rset = 2.5kOhm)
    """
    CHANNELS = 1

    def __init__(self, bus=1, i2c_addr=0x58, full_scale=0.025):
        super().__init__(self.RESOLUTION_12_BIT, bus=bus,
                         device_addr=i2c_addr, auto_range=False)
        self._full_scale_ma = full_scale * 1000
        self._dac_4 = 0
        self._dac_20 = 0
        self._calibration = False

    def calibration4_20ma(self, dac_4=655, dac_20=3277):
        """
        Calibrate the current with the DAC codes measured at 4mA and 20mA
        - param dac_4: Range 0-0xFFF, the calibration is invalid if the value is out of range, the DAC value corresponding to current of 4mA generally fluctuates at about 655, the actual value needs to be tested by the user in actual applications
        - param dac_20: Range 0-0xFFF, the calibration is invalid if the value is out of range, the DAC value corresponding to current of 20mA generally fluctuates at about 3277, the actual value needs to be tested by the user in actual applications
        """
        if not 0 <= dac_4 < dac_20 <= self._resolution:
            warnings.warn("Invalid calibration ignored, 0 <= dac_4 < dac_20 <= 0xFFF required.")
            return None
        self._dac_4 = dac_4
        self._dac_20 = dac_20
        self._calibration = True
        return None

    def set_dac_out_electric_current(self, current: float):
        """
        Set the output current, calibrated after calibration4_20ma()
        - param current [float]: output current in A (e.g. 0.012)
        - return [int]: the DAC code written
        """
        current_ma = min(max(float(current) * 1000, 0), self._full_scale_ma)
        if self._calibration:
            # Straight line through the 4mA and 20mA calibration points
            code = self._dac_4 + (current_ma - 4) * (self._dac_20 - self._dac_4) / 16
        else:
            code = current_ma / self._full_scale_ma * self._resolution
        code = min(max(round(code), 0), self._resolution)
        self._write_code(0, code)
        return code

    def set_dac_outrange(self, output_range: int = GP8XXX.OUTPUT_RANGE_10V):
        """
        Deprecated: the GP8302 has no output range, the call is ignored
        """
        warnings.warn("GP8302 has no output range, the call is ignored.", DeprecationWarning)

    def set_dac_out_voltage(self, voltage: float, channel: int = 0):
        """
        Deprecated: writes the DAC code of voltage at a 2.5V full scale,
        use set_dac_out_electric_current()
        """
        warnings.warn("Use set_dac_out_electric_current() for the GP8302.", DeprecationWarning)
        self._channels(channel)
        code = round(min(max(float(voltage), 0), 2.5) / 2.5 * self._resolution)
        self._write_code(0, code)
