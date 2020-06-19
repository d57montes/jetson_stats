# -*- coding: UTF-8 -*-
# This file is part of the jetson_stats package (https://github.com/rbonghi/jetson_stats or http://rnext.it).
# Copyright (c) 2019 Raffaello Bonghi.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import re
import os
from math import ceil
from .exceptions import JtopException
# Logging
import logging
# Compile decoder PWM table
FAN_PWM_TABLE_RE = re.compile(r'\((.*?)\)')
# Create logger
logger = logging.getLogger(__name__)

# Fan configurations
CONFIGS = ['jetson_clocks', 'manual', 'system']
CONFIG_DEFAULT_FAN_MODE = 'jetson_clocks'
CONFIG_DEFAULT_FAN_SPEED = 0.0


def load_table(path):
    table = []
    with open(path + 'pwm_rpm_table', 'r') as fp:
        title = []
        for line in fp.readlines():
            match = FAN_PWM_TABLE_RE.search(line)
            line = [tab.strip() for tab in match.group(1).split(",")]
            if title:
                table += [{title[idx]: val for idx, val in enumerate(line) if idx > 0}]
            else:
                title = line
    return table


def locate_fan():
    for fan in ['/sys/kernel/debug/tegra_fan/', '/sys/devices/pwm-fan/']:
        if os.path.isdir(fan):
            logger.info('Fan folder in {}'.format(fan))
            return fan
    raise JtopException('No Fans availabe on this board')


class Fan(object):

    def __init__(self):
        # Initialize number max records to record
        locate_fan()
        # Initialize fan
        self._status = {}
        self._mode = ''

    def __setattr__(self, name, value):
        if name not in ['mode', 'speed']:
            return object.__setattr__(self, name, value)
        # Don't send a message if value is the same
        if value == self._status[name]:
            return object.__setattr__(self, name, value)
        # Otherwise send the new speed to the server
        if name == 'mode':
            if value not in CONFIGS:
                raise JtopException('Control does not available')
        elif name == 'speed':
            if not isinstance(value, (int, float)):
                raise ValueError("Use a number")
            # Check limit speed
            if value < 0.0 or value > 100.0:
                raise ValueError('Wrong speed. Number between [0, 100]')
        # Set new jetson_clocks configuration
        self._controller.put({'fan': {name: value}})

    def _update(self, status):
        self._status = status
        # Load status as a dictionary
        self.__dict__.update(**self._status)

    def _init(self, controller):
        self._controller = controller

    def __repr__(self):
        return str(self._status)


class FanService(object):
    """
    Fan controller

    Unused variables:
     - table
     - step
    """
    def __init__(self, config):
        # Configuration
        self._config = config
        # Initialize number max records to record
        self.path = locate_fan()
        # Init status fan
        self.isRPM = os.path.isfile(self.path + 'rpm_measured')
        self.isCPWM = os.path.isfile(self.path + 'cur_pwm')
        self.isTPWM = os.path.isfile(self.path + 'target_pwm')
        self.isCTRL = os.path.isfile(self.path + 'temp_control')
        # Initialize dictionary status
        self._status = {}
        # Max value PWM
        self._pwm_cap = float(self._read_status('pwm_cap')) if os.path.isfile(self.path + 'pwm_cap') else 255
        # PWM RPM table
        self.table = load_table(self.path) if os.path.isfile(self.path + 'pwm_rpm_table') else {}
        # Step time
        self.step = int(self._read_status('step_time')) if os.path.isfile(self.path + 'step_time') else 0

    def initialization(self, jc):
        self._jc = jc
        # Load configuration
        config_fan = self._config.get('fan', {})
        # Set default speed
        self._control = config_fan.get('control', CONFIG_DEFAULT_FAN_SPEED)
        # Set default mode
        self.mode = config_fan.get('mode', CONFIG_DEFAULT_FAN_MODE)

    @property
    def mode(self):
        config_fan = self._config.get('fan', {})
        return config_fan.get('mode', CONFIG_DEFAULT_FAN_MODE)

    @mode.setter
    def mode(self, value):
        if value not in CONFIGS:
            raise JtopException('This control does not available')
        if value == 'system':
            self.auto = True
        if value == 'jetson_clocks':
            # Set in auto only if jetson_clocks in not active
            if not self._jc.is_alive:
                self.auto = True
        if value == 'manual':
            # Switch off speed
            self.auto = False
            # Initialize default speed
            self.control = self._control
        # Store mode
        self._status['mode'] = value
        # Extract configuration
        config = self._config.get('fan', {})
        # Add new value
        config['mode'] = value
        # Set new jetson_clocks configuration
        self._config.set('fan', config)

    @property
    def speed(self):
        if not self.isTPWM:
            raise JtopException('You can not set a speed for this fan')
        return self._status['speed']

    @speed.setter
    def speed(self, value):
        # Check type
        if not isinstance(value, (int, float)):
            raise ValueError('Need a number')
        # Check limit speed
        if value < 0.0 or value > 100.0:
            raise ValueError('Wrong speed. Number between [0, 100]')
        # Convert in PWM
        pwm = self._ValueToPWM(value)
        # Write PWM value
        with open(self.path + 'target_pwm', 'w') as f:
            f.write(str(pwm))
        # Extract configuration
        config = self._config.get('fan', {})
        # Add new value
        config['speed'] = value
        # Set new jetson_clocks configuration
        self._config.set('fan', config)

    @property
    def auto(self):
        return self._status.get('auto', False)

    @auto.setter
    def auto(self, value):
        if not isinstance(value, bool):
            raise ValueError('Need a boolean')
        # Check limit speed
        value = 1 if value else 0
        # Write status control value
        with open(self.path + 'temp_control', 'w') as f:
            f.write(str(value))

    def _PWMtoValue(self, pwm):
        return float(pwm) * 100.0 / self._pwm_cap

    def _ValueToPWM(self, value):
        return ceil(self._pwm_cap * value / 100.0)

    def update(self):
        # Control temperature
        if self.isCTRL:
            self._status['auto'] = bool(int(self._read_status('temp_control')) == 1)
        # Read PWM
        if self.isTPWM:
            self._status['speed'] = self._PWMtoValue(self._read_status('target_pwm'))
        # Read current
        if self.isCPWM:
            self._status['measure'] = self._PWMtoValue(self._read_status('cur_pwm'))
        # Read RPM fan
        if self.isRPM:
            self._status['rpm'] = int(self._read_status('rpm_measured'))
        return self._status

    def _read_status(self, file_read):
        with open(self.path + file_read, 'r') as f:
            return f.read()
        return None
# EOF
