# GP8XXX-IIC

The GP8XXX-IIC module provides an interface for controlling DAC (Digital to Analog Converter) devices over the I2C protocol. This module supports various DAC models with different resolutions and output voltage ranges.

## Features

- **I2C Communication**: Utilizes the smbus2 library for communicating with DAC devices over the I2C bus.
- **Output Ranges**: Supports 0-5V/0-10V (with automatic range selection), 0-2.5V/0-VCC and 0-25mA/4-20mA.
- **Support Multiple DAC Models**:
  - GP8302: 12-bit DAC I2C to 0-25mA/4-20mA
  - GP8211S: 15-bit DAC I2C to 0-5V/0-10V
  - GP8512: 15-bit DAC I2C to 0-2.5V/0-VCC
  - GP8503: 12-bit DAC Dual Channel I2C to 0-2.5V/0-VCC
  - GP8413: 15-bit DAC Dual Channel I2C to 0-5V/0-10V
  - GP8403: 12-bit DAC Dual Channel I2C to 0-5V/0-10V

## Installation
You can install the GP8XXX module from PyPI using pip:

```bash
pip install GP8XXX-IIC
```

## Example
```python
import time
from GP8XXX_IIC import GP8403

dac = GP8403(i2c_addr=0x5F)

while dac.begin():
    print("init error")
    time.sleep(1)

# Optional, the range is selected automatically by default (auto_range=True)
# dac.set_dac_outrange(dac.OUTPUT_RANGE_10V)

# Channel 0: 6.821V
dac.set_dac_out_voltage(voltage=6.821, channel=0)

time.sleep(3)

# Channel 1: 2.774V
dac.set_dac_out_voltage(voltage=2.774, channel=1)

time.sleep(3)

# Channel 0 & 1: 0.253V
dac.set_dac_out_voltage(voltage=0.253, channel=2)
```

Voltages outside of the output range are clamped. With `auto_range=False` call `set_dac_outrange()` before setting a voltage.

### GP8503 / GP8512 (0-2.5V/0-VCC)
The range is selected by the REFEN switch on the module, not by software. With the switch on 0-VCC pass the supply voltage as full scale:

```python
from GP8XXX_IIC import GP8512

dac = GP8512(vref=5.0)  # default vref=2.5 for the 0-2.5V switch position
dac.set_dac_out_voltage(1.9)
```

### GP8302 (0-25mA / 4-20mA)
The current is set in A. The full scale is 50V / Rset (0.025A with Rset = 2kOhm on the DFRobot module, pass `full_scale=0.020` for Rset = 2.5kOhm). For an accurate 4-20mA signal measure the DAC codes for 4mA and 20mA and pass them to `calibration4_20ma()`.

```python
from GP8XXX_IIC import GP8302

dac = GP8302()
dac.calibration4_20ma(dac_4=655, dac_20=3277)
dac.set_dac_out_electric_current(0.012)  # 12mA
```

## Store output values
The chip can save the current output so it's restored after a power cycle. The store sequence isn't valid I2C (3-bit frame, unacknowledged bytes), so it's bit-banged on the SDA/SCL pins with `lgpio` (preinstalled on Raspberry Pi OS Bookworm, works on the Pi 5). The pin functions are read with `pinctrl` beforehand and restored afterwards, so the hardware I2C bus keeps working.

```bash
pip install GP8XXX-IIC[store]  # or: sudo apt install python3-lgpio
```

```python
dac.set_dac_out_voltage(voltage=6.821, channel=0)
dac.store()  # bus 1: SDA=GPIO2, SCL=GPIO3 (BCM)
dac.store(sda_pin=22, scl_pin=23)  # other buses: pass the pins of the bus
```

> [!NOTE]
> While storing (~15ms) the pins are taken away from the I2C controller. Other programs using the same bus get I2C errors during that time, and the bit-banged frames are seen by every device on the bus (the sequence also writes to address `0x08`). Call `store()` once while nothing else uses the bus, or put the DAC on its own bus.

## Tested devices

| DAC Module | Tested |
|------------|--------|
|GP8503      | ❌     |
|GP8211S     | ❌     |
|GP8512      | ❌     |
|GP8413      | ✅     |
|GP8403      | ✅     |
|GP8302      | ❌     |
