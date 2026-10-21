# pylint: disable=protected-access

import unittest
from unittest.mock import MagicMock, patch
from GP8XXX_IIC import GP8503, GP8211S, GP8512, GP8413, GP8403, GP8302


class TestGP8XXX(unittest.TestCase):
    """
    Test cases for GP8XXX classes.
    """

    def setUp(self):
        self.mock_i2c = MagicMock()
        patcher = patch('GP8XXX_IIC.SMBus', return_value=self.mock_i2c)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_last_write(self, register, data):
        """
        Check the last data written to a register
        """
        writes = [call.args for call in self.mock_i2c.write_word_data.call_args_list
                  if call.args[1] == register]
        self.assertTrue(writes, f"Register {register:#04x} was never written")
        self.assertEqual(writes[-1], (0x58, register, data))

    def test_begin(self):
        dac = GP8403()
        self.assertTrue(dac.begin())
        self.mock_i2c.write_quick.assert_called_once_with(0x58)

        self.mock_i2c.write_quick.side_effect = OSError(121, 'Remote I/O error')
        self.assertFalse(dac.begin())

    def test_gp8503(self):
        """
        12 bit, left-aligned data (<<4), fixed 2.5V full scale.
        """
        gp8503 = GP8503()

        gp8503.set_dac_out_voltage(2.0, 0)
        self.assertEqual(gp8503.channel0['value'], 2000)
        self.assertEqual(gp8503.channel0['dac_voltage'], 2500)
        self.mock_i2c.write_word_data.assert_any_call(0x58, 0x02, 3276 << 4)

        gp8503.set_dac_out_voltage(0.167, 0)
        self.assertEqual(gp8503.channel0['value'], 167)
        self.assert_last_write(0x02, 274 << 4)

        gp8503.set_dac_out_voltage(9.8, 0)
        self.assertEqual(gp8503.channel0['value'], 2500)
        self.assert_last_write(0x02, 0x0FFF << 4)

        self.assertEqual(gp8503.channel1['value'], 0)
        self.assertEqual(gp8503.channel1['dac_voltage'], 2500)

        gp8503.set_dac_out_voltage(1.8, 1)
        self.assertEqual(gp8503.channel1['value'], 1800)
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

        # Only the register of channel 0 is written
        for call in self.mock_i2c.write_word_data.call_args_list:
            self.assertEqual(call.args[1], 0x02)

        for channel in (1, 2):
            with self.assertRaises(ValueError) as context:
                gp8211s.set_dac_out_voltage(3.5, channel)
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
        self.mock_i2c.write_byte_data.assert_called_once_with(0x5B, 0x01, 0x11)

        gp8413.set_dac_out_voltage(3.5, 0)
        self.assertEqual(gp8413.channel0['value'], 3500)
        self.assertEqual(gp8413.channel0['dac_voltage'], 5000)
        self.mock_i2c.write_word_data.assert_any_call(0x5B, 0x02, 22937 << 1)

        gp8413.set_dac_out_voltage(9.2, 0)
        self.assertEqual(gp8413.channel0['value'], 9200)
        self.assertEqual(gp8413.channel0['dac_voltage'], 10000)
        self.mock_i2c.write_word_data.assert_any_call(0x5B, 0x02, 30146 << 1)

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
        self.mock_i2c.write_word_data.assert_any_call(0x58, 0x02, 1638 << 4)

        gp8403.set_dac_out_voltage(0.167, 0)
        self.assertEqual(gp8403.channel0['value'], 167)
        self.assertEqual(gp8403.channel0['dac_voltage'], 5000)

        gp8403.set_dac_out_voltage(5.01, 0)
        self.assertEqual(gp8403.channel0['value'], 5010)
        self.assertEqual(gp8403.channel0['dac_voltage'], 10000)

        # The range change also rewrites channel 1
        self.assertEqual(gp8403.channel1['value'], 0)
        self.assertEqual(gp8403.channel1['dac_voltage'], 10000)

        gp8403.set_dac_out_voltage(7.8321, 1)
        self.assertAlmostEqual(gp8403.channel1['value'], 7832.1)
        self.assertEqual(gp8403.channel1['dac_voltage'], 10000)
        self.assert_last_write(0x04, 3207 << 4)

        with self.assertRaises(ValueError):
            gp8403.set_dac_out_voltage(1.0, 3)
        with self.assertRaises(ValueError):
            gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_2_5V)

    def test_gp8403_all_channels(self):
        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(0.253, 2)
        self.mock_i2c.write_word_data.assert_any_call(0x58, 0x02, 207 << 4)
        self.mock_i2c.write_word_data.assert_any_call(0x58, 0x04, 207 << 4)

    def test_auto_range_written_only_on_change(self):
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

    def test_voltage_clamped_to_range(self):
        gp8403 = GP8403(auto_range=False)
        gp8403.set_dac_outrange(gp8403.OUTPUT_RANGE_5V)
        gp8403.set_dac_out_voltage(7.0, 0)
        self.assert_last_write(0x02, 0x0FFF << 4)

        gp8403 = GP8403()
        gp8403.set_dac_out_voltage(12.0, 0)
        self.assertEqual(gp8403.channel0['value'], 10000)
        self.assert_last_write(0x02, 0x0FFF << 4)

        gp8403.set_dac_out_voltage(-1.0, 0)
        self.assertEqual(gp8403.channel0['value'], 0)

    def test_range_required_without_auto_range(self):
        gp8403 = GP8403(auto_range=False)
        with self.assertRaises(RuntimeError):
            gp8403.set_dac_out_voltage(1.0, 0)
        self.mock_i2c.write_word_data.assert_not_called()

    def test_gp8302(self):
        """
        Current output 0-25mA (Rset = 2kOhm), 12 bit left-aligned.
        """
        gp8302 = GP8302()

        self.assertEqual(gp8302.set_dac_out_electric_current(20), 3276)
        self.assert_last_write(0x02, 3276 << 4)

        self.assertEqual(gp8302.set_dac_out_electric_current(30), 0x0FFF)
        self.assertEqual(gp8302.set_dac_out_electric_current(-1), 0)

        with self.assertRaises(ValueError):
            gp8302.set_dac_outrange(gp8302.OUTPUT_RANGE_5V)
        with self.assertRaises(NotImplementedError):
            gp8302.set_dac_out_voltage(2.0)

    def test_gp8302_calibration(self):
        gp8302 = GP8302()
        gp8302.calibration4_20ma(dac_4=655, dac_20=3277)

        self.assertEqual(gp8302.set_dac_out_electric_current(4), 655)
        self.assertEqual(gp8302.set_dac_out_electric_current(20), 3277)
        self.assertEqual(gp8302.set_dac_out_electric_current(12), 1966)
        self.assert_last_write(0x02, 1966 << 4)

        # Outside of 4-20mA the uncalibrated full scale is used
        self.assertEqual(gp8302.set_dac_out_electric_current(25), 0x0FFF)

        with self.assertRaises(ValueError):
            gp8302.calibration4_20ma(dac_4=3277, dac_20=655)

    def test_gp8302_full_scale(self):
        gp8302 = GP8302(full_scale_ma=20)
        self.assertEqual(gp8302.set_dac_out_electric_current(10), 2048)


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
        self.closed = False

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

    def close(self):
        self.closed = True


class TestStore(unittest.TestCase):
    """
    Test the store sequence against the timing diagram of the datasheet (3.3.7).
    """

    @patch('GP8XXX_IIC.time.sleep')
    @patch('GP8XXX_IIC.SMBus')
    def test_store_sequence(self, mock_smbus, mock_sleep):
        pins = FakePins(sda=2, scl=3)
        gp8403 = GP8403()
        gp8403.store(pins=pins)

        nack = '1'
        expected = (
            'S010P'
            'S00010000' + nack + '00000011' + nack + 'P'
            'S10110000' + nack + ('00000000' + nack) * 8 + 'P'
            'S010P'
            'S00010000' + nack + '00000000' + nack + 'P'
        )
        self.assertEqual(pins.trace, expected)
        self.assertTrue(pins.closed)
        mock_sleep.assert_any_call(gp8403.GP8XXX_STORE_TIMING_DELAY)

    @patch('GP8XXX_IIC.time.sleep')
    @patch('GP8XXX_IIC.SMBus')
    def test_store_uses_device_address(self, mock_smbus, mock_sleep):
        pins = FakePins(sda=2, scl=3)
        GP8403(i2c_addr=0x5F).store(pins=pins)
        self.assertIn('S10111110', pins.trace)

    @patch('GP8XXX_IIC.time.sleep')
    @patch('GP8XXX_IIC.SMBus')
    @patch('GP8XXX_IIC.shutil.which', return_value='/usr/bin/pinctrl')
    @patch('GP8XXX_IIC.subprocess.run')
    @patch('GP8XXX_IIC._LgpioPins')
    def test_store_restores_pin_functions(self, mock_lgpio_pins, mock_run, mock_which,
                                          mock_smbus, mock_sleep):
        mock_run.side_effect = lambda args, **kwargs: MagicMock(
            stdout=f" {args[2]}: a3    pu | hi // GPIO{args[2]}\n")
        mock_lgpio_pins.return_value.set.side_effect = OSError('bus error')

        with self.assertRaises(OSError):
            GP8403().store()

        mock_lgpio_pins.return_value.close.assert_called_once()
        mock_run.assert_any_call(['pinctrl', 'set', '2', 'a3', 'pu'], check=True)
        mock_run.assert_any_call(['pinctrl', 'set', '3', 'a3', 'pu'], check=True)

    @patch('GP8XXX_IIC.SMBus')
    @patch('GP8XXX_IIC.shutil.which', return_value=None)
    @patch('GP8XXX_IIC._LgpioPins')
    def test_store_without_pinctrl_keeps_bus_untouched(self, mock_lgpio_pins, mock_which,
                                                       mock_smbus):
        with self.assertRaises(RuntimeError):
            GP8403().store()
        mock_lgpio_pins.assert_not_called()


if __name__ == '__main__':
    unittest.main()
