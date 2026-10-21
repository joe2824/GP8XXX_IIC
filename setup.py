from setuptools import setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="GP8XXX_IIC",
    keywords='Raspberry Pi, Raspi, GP8403, GP8503, GP8211S, GP8512, GP8413, GP8302, DAC',
    version="0.0.5",
    author="Joel Klein",
    description="The GP8XXX Python module offers an intuitive interface for controlling DAC (Digital to Analog Converter) devices via the I2C protocol. With support for various DAC models.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/joe2824/GP8XXX_IIC/",
    project_urls={
        "Bug Tracker": "https://github.com/joe2824/GP8XXX_IIC/issues",
        "Github": "https://github.com/joe2824/GP8XXX_IIC/",
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        "Programming Language :: Python :: 3",
        'License :: OSI Approved :: MIT License',
        "Operating System :: POSIX :: Linux",
    ],
    packages=['GP8XXX_IIC'],
    python_requires=">=3.8",
    install_requires=[
        'smbus2'
    ],
    extras_require={
        # Needed for store()
        'store': ['lgpio'],
    }
)
