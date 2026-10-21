# pylint: disable=protected-access

import subprocess
import unittest
import warnings
from unittest.mock import MagicMock, call, patch
from GP8XXX_IIC import GP8503, GP8211S, GP8512, GP8413, GP8403, GP8302
from GP8XXX_IIC import store


class TestGP8XXX(unittest.TestCase):
    """
    Test cases for GP8XXX classes. The tests check the data written to the bus.
    """

    def setUp(self):
        self.mock_i2c = MagicMock()
        patcher = patch('GP8XXX_IIC.SMBus', return_value=self.mock_i2c)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_last_write(self, register, data, addr=0x58):
        """
        Check the last data written to a register
        """
        writes = [c.args for c in self.mock_i2c.write_word_data.call_args_list
                  if c.args[1] == register]
        self.assertTrue(writes, f"Register {register:#04x} was never written")
        self.assertEqual(writes[-1], (addr, register, data))

    def bus_writes(self):
        """
        All writes in order, as (register, data)
        """
        return [c.args[1:] for c in self.mock_i2c.mock_calls
                if c[0] in ('write_byte_data', 'write_word_data')]

    def test_begin(self):
        """
        begin() returns 0 for success like the README loop expects
        """
        dac = GP8403()
        self.assertEqual(dac.begin(), 0)
        self.mock_i2c.write_quick.assert_called_once_with(0x58)

        self.mock_i2c.write_quick.side_effect = OSError(121, 'Remote I/O error')
        self.assertEqual(dac.begin(), 1)

    def test_gp8503(self):
        """
        12 bit, left-aligned data (<<4), fixed 2.5V full scale.
        """
        gp8503 = GP8503()

        gp8503.set_dac_out_voltage(2.0, 0)
        self.assertEqual(gp8503.channel0['value'], 2000)
        self.assertEqual(gp8503.channel0['dac_voltage'], 2500)
        self.assert_last_write(0x02, 3276 << 4)

        gp8503.set_dac_out_voltage(0.167, 0)
        self.assertEqual(gp8503.channel0['value'], 167)
        self.assert_last_write(0x02, 274 << 4)

        gp8503.set_dac_out_voltage(9.8, 0)
        self.assertEqual(gp8503.channel0['value'], 2500)
        self.assert_last_write(0x02, 0x0FFF << 4)

        # Channel 1 isn't touched until it is set
        self.assertEqual(gp8503.channel1['value'], 0)
        self.assertIsNone(gp8503.channel1['dac_voltage'])

        gp8503.set_dac_out_voltage(1.8, 1)
        self.assertEqual(gp8503.channel1['value'], 1800)
        self.assertEqual(gp8503.channel1['dac_voltage'], 2500)
        self.assert_last_write(0x04, 2948 << 4)

        with self.assertRaises(ValueError) as context:
            gp8503.set_dac_outrange(gp8503.OUTPUT_RANGE_10V)
        self.assertEqual(str(context.exception),
                         "DAC doesn't support another output range.")
        self.mock_i2c.write_byte_data.assert_not_called()

    def test_gp8503_vref(self):
        """
        With the 0-VCC switch the full scale is the supply voltage.
        """
        gp8503 = GP8503(vref=5.0)
        gp8503.set_dac_out_voltage(2.5, 0)
        self.assert_last_write(0x02, 2048 << 4)

    def test_gp8211s(self):
        """
        15 bit, single channel, 0-5V/0-10V.
        """
        gp8211s = GP8211S()

        gp8211s.set_dac_outrange(gp8211s.OUTPUT_RANGE_10V)
        self.assertEqual(gp8211s._dac_voltage, 10000)
        self.mock_i2c.write_byte_data.assert_called_with(0x58, 0x01, 0x11)

        gp8211s.set_dac_outrange(gp8211s.OUTPUT_RANGE_5V)
        self.assertEqual(gp8211s._dac_voltage, 5000)
        self.mock_i2c.write_byte_data.assert_called_with(0x58, 0x01, 0x00)

        gp8211s.set_dac_out_voltage(3.5, 0)
        self.assertEqual(gp8211s.channel0['value'], 3500)
        self.assertEqual(gp8211s.channel0['dac_voltage'], 5000)
        self.assert_last_write(0x02, 22937 << 1)

        gp8211s.set_dac_out_voltage(9.5, 0)
        self.assertEqual(gp8211s.channel0['value'], 9500)
        self.assertEqual(gp8211s.channel0['dac_voltage'], 10000)
        self.assert_last_write(0x02, 31129 << 1)

        # "All channels" is the only channel
        gp8211s.set_dac_out_voltage(2.0, 2)
        self.assert_last_write(0x02, 13107 << 1)

        # Only the register of channel 0 is written
        for c in self.mock_i2c.write_word_data.call_args_list:
            self.assertEqual(c.args[1], 0x02)

        with self.assertRaises(ValueError) as context:
            gp8211s.set_dac_out_voltage(3.5, 1)
        self.assertEqual(str(context.exception),
                         "Unsupported channel. The DAC only supports channel 0.")

    def test_gp8512(self):
        """
        15 bit, single channel, right-aligned data (no shift), fixed 2.5V full scale.
        """
        gp8512 = GP8512()

        gp8512.set_dac_out_voltage(2.0, 0)
        self.assertEqual(gp8512.channel0['value'], 2000)
        self.assertEqual(gp8512.channel0['dac_voltage'], 2500)
        self.assert_last_write(0x02, 26214)

        # Example of the DFRobot wiki: 24903 gives 1.9V
        gp8512.set_dac_out_voltage(1.9, 0)
        self.assert_last_write(0x02, 24903)

        gp8512.set_dac_out_voltage(9.8, 0)
        self.assertEqual(gp8512.channel0['value'], 2500)
        self.assert_last_write(0x02, 0x7FFF)

        with self.assertRaises(ValueError) as context:
            gp8512.set_dac_out_voltage(1.8, 1)
        self.assertEqual(str(context.exception),
                         "Unsupported channel. The DAC only supports channel 0.")

        with self.assertRaises(ValueError) as context:
            gp8512.set_dac_outrange(gp8512.OUTPUT_RANGE_5V)
        self.assertEqual(str(context.exception),
                         "DAC doesn't support another output range.")

    def test_gp8413(self):
        """
        15 bit, dual channel, left-aligned data (<<1), address pins.
        """
        gp8413 = GP8413(bus=1, i2c_addr=0x5B)
        # The constructor only sets the range and keeps the outputs
        self.mock_i2c.write_byte_data.assert_called_once_with(0x5B, 0x01, 0x11)
        self.mock_i2c.write_word_data.assert_not_called()

        gp8413.set_dac_out_voltage(3.5, 0)
        self.assertEqual(gp8413.channel0['value'], 3500)
        self.assertEqual(gp8413.channel0['dac_voltage'], 5000)
        self.assert_last_write(0x02, 22937 << 1, addr=0x5B)

        gp8413.set_dac_out_voltage(9.2, 0)
        self.assertEqual(gp8413.channel0['value'], 9200)
        self.assertEqual(gp8413.channel0['dac_voltage'], 10000)
        self.assert_last_write(0x02, 30146 << 1, addr=0x5B)

    def test_gp8403(self):
        """
        12 bit, dual channel, auto range.
        """
        gp8403 = GP8403()

        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_10V)
        self.assertEqual(gp8403._dac_voltage, 10000)

        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_5V)
        self.assertEqual(gp8403._dac_voltage, 5000)

        gp8403.set_dac_out_voltage(2.0, 0)
        self.assertEqual(gp8403.channel0['value'], 2000)
        self.assertEqual(gp8403.channel0['dac_voltage'], 5000)
        self.assert_last_write(0x02, 1638 << 4)

        gp8403.set_dac_out_voltage(0.167, 0)
        self.assertEqual(gp8403.channel0['value'], 167)
        self.assertEqual(gp8403.channel0['dac_voltage'], 5000)

        gp8403.set_dac_out_voltage(5.01, 0)
        self.assertEqual(gp8403.channel0['value'], 5010)
        self.assertEqual(gp8403.channel0['dac_voltage'], 10000)

        gp8403.set_dac_out_voltage(7.8321, 1)
        self.assertAlmostEqual(gp8403.channel1['value'], 7832.1)
        self.assertEqual(gp8403.channel1['dac_voltage'], 10000)
        self.assert_last_write(0x04, 3207 << 4)

    def test_gp8403_all_channels(self):
        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(0.253, 2)
        self.assert_last_write(0x02, 207 << 4)
        self.assert_last_write(0x04, 207 << 4)

    def test_invalid_channel_and_range_ignored(self):
        """
        Like before, invalid values are ignored, now with a warning
        """
        gp8403 = GP8403()
        with self.assertWarns(UserWarning):
            gp8403.set_dac_out_voltage(1.0, 3)
        self.mock_i2c.write_word_data.assert_not_called()

        with self.assertWarns(UserWarning):
            gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_2_5V)
        self.mock_i2c.write_byte_data.assert_not_called()

    def test_range_up_writes_codes_first(self):
        """
        5V -> 10V: the halved codes are written before the range, so no channel
        outputs more than requested in between
        """
        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(4.5, 1)
        self.mock_i2c.reset_mock()

        gp8403.set_dac_out_voltage(6.0, 0)
        self.assertEqual(self.bus_writes(), [
            (0x02, 2457 << 4),
            (0x04, 1843 << 4),
            (0x01, 0x11),
        ])

    def test_range_down_writes_range_first(self):
        """
        10V -> 5V: the range is written before the doubled codes
        """
        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(6.0, 0)
        gp8403.set_dac_out_voltage(4.5, 1)
        self.mock_i2c.reset_mock()

        gp8403.set_dac_out_voltage(1.0, 0)
        self.assertEqual(self.bus_writes(), [
            (0x01, 0x00),
            (0x02, 819 << 4),
            (0x04, 3686 << 4),
        ])

    def test_range_written_only_on_change(self):
        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(1.0, 0)
        gp8403.set_dac_out_voltage(2.0, 0)
        gp8403.set_dac_out_voltage(3.0, 1)
        self.mock_i2c.write_byte_data.assert_called_once_with(0x58, 0x01, 0x00)

        # The range stays 10V as long as one channel needs it
        gp8403.set_dac_out_voltage(8.0, 1)
        gp8403.set_dac_out_voltage(1.0, 0)
        self.mock_i2c.write_byte_data.assert_called_with(0x58, 0x01, 0x11)
        self.assertEqual(self.mock_i2c.write_byte_data.call_count, 2)

    def test_set_dac_outrange_keeps_voltages(self):
        gp8403 = GP8403(auto_range=False)
        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_5V)
        gp8403.set_dac_out_voltage(4.0, 0)
        self.mock_i2c.reset_mock()

        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_10V)
        self.assertEqual(self.bus_writes(), [(0x02, 1638 << 4), (0x01, 0x11)])

    def test_voltage_clamped_to_range(self):
        gp8403 = GP8403(auto_range=False)
        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_5V)
        gp8403.set_dac_out_voltage(7.0, 0)
        self.assertEqual(gp8403.channel0['value'], 5000)
        self.assert_last_write(0x02, 0x0FFF << 4)

        # The clamped voltage is kept after a range change
        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_10V)
        self.assert_last_write(0x02, 2048 << 4)

        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(12.0, 0)
        self.assertEqual(gp8403.channel0['value'], 10000)

        gp8403.set_dac_out_voltage(-1.0, 0)
        self.assertEqual(gp8403.channel0['value'], 0)

    def test_range_required_without_auto_range(self):
        gp8403 = GP8403(auto_range=False)
        with self.assertRaises(RuntimeError):
            gp8403.set_dac_out_voltage(1.0, 0)
        self.mock_i2c.write_word_data.assert_not_called()

    def test_gp8302(self):
        """
        Current output in A, 0-25mA (Rset = 2kOhm), 12 bit left-aligned.
        """
        gp8302 = GP8302()

        self.assertEqual(gp8302.set_dac_out_electric_current(0.020), 3276)
        self.assert_last_write(0x02, 3276 << 4)

        self.assertEqual(gp8302.set_dac_out_electric_current(0.030), 0x0FFF)
        self.assertEqual(gp8302.set_dac_out_electric_current(-1), 0)

    def test_gp8302_deprecated_methods(self):
        gp8302 = GP8302()
        with self.assertWarns(DeprecationWarning):
            gp8302.set_dac_outrange(gp8302.OUTPUT_RANGE_5V)
        self.mock_i2c.write_byte_data.assert_not_called()

        # Same DAC code as before: voltage at a 2.5V full scale
        with self.assertWarns(DeprecationWarning):
            gp8302.set_dac_out_voltage(2.0)
        self.assert_last_write(0x02, 3276 << 4)

    def test_gp8302_calibration(self):
        gp8302 = GP8302()
        gp8302.calibration4_20ma(dac_4=655, dac_20=3277)

        self.assertEqual(gp8302.set_dac_out_electric_current(0.004), 655)
        self.assertEqual(gp8302.set_dac_out_electric_current(0.020), 3277)
        self.assertEqual(gp8302.set_dac_out_electric_current(0.012), 1966)
        self.assert_last_write(0x02, 1966 << 4)

    def test_gp8302_calibration_monotonic(self):
        """
        No jumps at 4mA and 20mA, the calibration line is used everywhere
        """
        gp8302 = GP8302()
        gp8302.calibration4_20ma(dac_4=600, dac_20=3400)
        currents = [0, 0.00399, 0.004, 0.020, 0.02001, 0.025]
        codes = [gp8302.set_dac_out_electric_current(c) for c in currents]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(codes[2:4], [600, 3400])

    def test_gp8302_invalid_calibration_ignored(self):
        gp8302 = GP8302()
        with self.assertWarns(UserWarning):
            self.assertIsNone(gp8302.calibration4_20ma(dac_4=3277, dac_20=655))
        self.assertEqual(gp8302.set_dac_out_electric_current(0.020), 3276)

    def test_gp8302_full_scale(self):
        gp8302 = GP8302(full_scale=0.020)
        self.assertEqual(gp8302.set_dac_out_electric_current(0.010), 2048)


class FakePins:
    """
    Records the open-drain SDA/SCL lines and decodes them into START (S),
    STOP (P) and bits. Nobody acknowledges, so a released SDA reads as 1.
    """

    def __init__(self, sda, scl):
        self.sda, self.scl = sda, scl
        self.level = {sda: 1, scl: 1}
        self.trace = ''
        self._pending_bit = None

    def set(self, pin, level):
        sda_before, scl_before = self.level[self.sda], self.level[self.scl]
        self.level[pin] = level
        sda, scl = self.level[self.sda], self.level[self.scl]
        if scl and scl_before and sda != sda_before:
            self.trace += 'P' if sda else 'S'
            self._pending_bit = None
        elif scl and not scl_before:
            self._pending_bit = sda
        elif not scl and scl_before and self._pending_bit is not None:
            self.trace += str(self._pending_bit)
            self._pending_bit = None


@patch('GP8XXX_IIC.store.time.sleep')
@patch('GP8XXX_IIC.SMBus')
class TestStore(unittest.TestCase):
    """
    Test the store sequence against the timing diagram of the datasheet (3.3.7).
    """

    def test_store_sequence(self, mock_smbus, mock_sleep):
        pins = FakePins(sda=2, scl=3)
        GP8403().store(pins=pins)

        nack = '1'
        expected = (
            # Bus recovery: 9 clocks with SDA released, STOP
            '111111111P'
            'S010P'
            'S00010000' + nack + '00000011' + nack + 'P'
            'S10110000' + nack + ('00000000' + nack) * 8 + 'P'
            'S010P'
            'S00010000' + nack + '00000000' + nack + 'P'
        )
        self.assertEqual(pins.trace, expected)
        mock_sleep.assert_any_call(store.STORE_TIMING_DELAY)

    def test_store_uses_device_address(self, mock_smbus, mock_sleep):
        pins = FakePins(sda=2, scl=3)
        GP8403(i2c_addr=0x5F).store(pins=pins)
        self.assertIn('S10111110', pins.trace)

    def test_store_on_other_bus_requires_pins(self, mock_smbus, mock_sleep):
        with self.assertRaises(ValueError):
            GP8403(bus=3).store()

        pins = FakePins(sda=22, scl=23)
        GP8403(bus=3).store(sda_pin=22, scl_pin=23, pins=pins)
        self.assertIn('S010P', pins.trace)

    @patch('GP8XXX_IIC.store.shutil.which', return_value='/usr/bin/pinctrl')
    @patch('GP8XXX_IIC.store.subprocess.run')
    @patch('GP8XXX_IIC.LgpioPins')
    def test_store_restores_pin_functions(self, mock_lgpio_pins, mock_run, mock_which,
                                          mock_smbus, mock_sleep):
        mock_run.side_effect = lambda args, **kwargs: MagicMock(
            stdout=f" {args[2]}:  a3    pu | hi // GPIO{args[2]} = SDA1\n")
        pins = mock_lgpio_pins.return_value.__enter__.return_value
        pins.set.side_effect = OSError('bus error')
        mock_lgpio_pins.return_value.__exit__.return_value = False

        with self.assertRaises(OSError):
            GP8403().store()

        mock_lgpio_pins.return_value.__exit__.assert_called_once()
        mock_run.assert_any_call(['pinctrl', 'set', '2', 'a3', 'pu'], check=True)
        mock_run.assert_any_call(['pinctrl', 'set', '3', 'a3', 'pu'], check=True)

    @patch('GP8XXX_IIC.store.shutil.which', return_value='/usr/bin/pinctrl')
    @patch('GP8XXX_IIC.store.subprocess.run')
    @patch('GP8XXX_IIC.LgpioPins')
    def test_store_restores_both_pins_on_error(self, mock_lgpio_pins, mock_run, mock_which,
                                               mock_smbus, mock_sleep):
        def run(args, **kwargs):
            if args[1] == 'set' and args[2] == '2':
                raise subprocess.CalledProcessError(1, args)
            return MagicMock(stdout=f" {args[2]}: a3    pu | hi // GPIO{args[2]}\n")
        mock_run.side_effect = run

        with self.assertRaises(RuntimeError):
            GP8403().store()
        mock_run.assert_any_call(['pinctrl', 'set', '3', 'a3', 'pu'], check=True)

    @patch('GP8XXX_IIC.store.shutil.which', return_value='/usr/bin/pinctrl')
    @patch('GP8XXX_IIC.store.subprocess.run')
    @patch('GP8XXX_IIC.LgpioPins')
    def test_store_unexpected_pinctrl_output_keeps_bus_untouched(
            self, mock_lgpio_pins, mock_run, mock_which, mock_smbus, mock_sleep):
        mock_run.return_value = MagicMock(stdout="something else\n")
        with self.assertRaises(RuntimeError):
            GP8403().store()
        mock_lgpio_pins.assert_not_called()
        self.assertNotIn(call(['pinctrl', 'set', '2', 'a3', 'pu'], check=True),
                         mock_run.call_args_list)

    @patch('GP8XXX_IIC.store.shutil.which', return_value=None)
    @patch('GP8XXX_IIC.LgpioPins')
    def test_store_without_pinctrl_keeps_bus_untouched(self, mock_lgpio_pins, mock_which,
                                                       mock_smbus, mock_sleep):
        with self.assertRaises(RuntimeError):
            GP8403().store()
        mock_lgpio_pins.assert_not_called()


class TestPinctrlParsing(unittest.TestCase):
    """
    pinctrl output formats of the Pi 5 and older models
    """

    @patch('GP8XXX_IIC.store.subprocess.run')
    def test_formats(self, mock_run):
        outputs = {
            " 2: a3    pu | hi // GPIO2 = SDA1\n": ['a3', 'pu'],
            " 2: a0    pu | hi // GPIO2 = SDA1\n": ['a0', 'pu'],
            " 2: op dl pd | lo // GPIO2 = output\n": ['op', 'dl', 'pd'],
            " 2: ip    pn | hi // GPIO2 = input\n": ['ip', 'pn'],
        }
        for output, expected in outputs.items():
            mock_run.return_value = MagicMock(stdout=output)
            self.assertEqual(store._get_pin_function(2), expected)

        mock_run.return_value = MagicMock(stdout=" 3: a3    pu | hi // GPIO3 = SCL1\n")
        with self.assertRaises(RuntimeError):
            store._get_pin_function(2)


if __name__ == '__main__':
    warnings.simplefilter('default')
    unittest.main()
