import pytest
import sys
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

import logging
logging.disable(logging.CRITICAL)

import argparse
from mkmassupdate import _positive_int, _port_type, _positive_float


class TestPositiveInt:
    def test_accepts_one(self):
        assert _positive_int('1') == 1

    def test_accepts_large(self):
        assert _positive_int('100') == 100

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int('-5')

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError):
            _positive_int('abc')


class TestPortType:
    def test_accepts_standard_port(self):
        assert _port_type('8728') == 8728

    def test_accepts_ssl_port(self):
        assert _port_type('8729') == 8729

    def test_accepts_min_port(self):
        assert _port_type('1') == 1

    def test_accepts_max_port(self):
        assert _port_type('65535') == 65535

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('-1')

    def test_rejects_overflow(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('65536')

    def test_rejects_large(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('99999')


class TestPositiveFloat:
    def test_accepts_positive_int_str(self):
        assert _positive_float('2') == 2.0

    def test_accepts_float(self):
        assert _positive_float('2.5') == 2.5

    def test_accepts_small_positive(self):
        assert _positive_float('0.1') == 0.1

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float('-1.5')
