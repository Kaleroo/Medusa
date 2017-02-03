# coding=utf-8

# Author: Rafael Silva <rpluto@gmail.com>
# Author: Marvin Pinto <me@marvinp.ca>
# Author: Dennis Lutter <lad1337@gmail.com>
#
# This file is part of Medusa.
#
# Medusa is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Medusa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Medusa. If not, see <http://www.gnu.org/licenses/>.
"""Boxcar2 module."""
from __future__ import unicode_literals

from .. import app, common, logger
from ..session.core import Session


class Notifier(object):
    """Boxcar2 class."""

    def __init__(self):
        """Initialize the class."""
        self.session = Session()
        self.url = 'https://new.boxcar.io/api/notifications'

    def test_notify(self, accesstoken, title='Medusa: Test'):
        """Test the notify."""
        return self._send_boxcar2('This is a test notification from Medusa', title, accesstoken)

    def _send_boxcar2(self, msg, title, accesstoken):
        """
        Send a boxcar2 notification to the address provided.

        msg: The message to send
        title: The title of the message
        accesstoken: to send to this device

        returns: True if the message succeeded, False otherwise
        """
        # http://blog.boxcar.io/post/93211745502/boxcar-api-update-boxcar-api-update-icon-and

        post_data = {
            'user_credentials': accesstoken,
            'notification[title]': 'Medusa: {}: {}'.format(title, msg),
            'notification[long_message]': msg,
            'notification[sound]': 'notifier-2',
            'notification[source_name]': 'Medusa',
            'notification[icon_url]': app.LOGO_URL
        }

        # TODO: SESSION: Check if this needs exception handling.
        response = self.session.post(self.url, data=post_data, timeout=60).json()
        if not response:
            logger.log('Boxcar2 notification failed.', logger.ERROR)
            return False

        logger.log('Boxcar2 notification successful.', logger.DEBUG)
        return True

    def notify_snatch(self, ep_name, is_proper):
        """Send the snatch message."""
        title = common.notifyStrings[(common.NOTIFY_SNATCH, common.NOTIFY_SNATCH_PROPER)[is_proper]]
        if app.BOXCAR2_NOTIFY_ONSNATCH:
            self._notify_boxcar2(title, ep_name)

    def notify_download(self, ep_name, title=common.notifyStrings[common.NOTIFY_DOWNLOAD]):
        """Send the download message."""
        if app.BOXCAR2_NOTIFY_ONDOWNLOAD:
            self._notify_boxcar2(title, ep_name)

    def notify_subtitle_download(self, ep_name, lang, title=common.notifyStrings[common.NOTIFY_SUBTITLE_DOWNLOAD]):
        """Send the subtitle download message."""
        if app.BOXCAR2_NOTIFY_ONSUBTITLEDOWNLOAD:
            self._notify_boxcar2(title, ep_name + ': ' + lang)

    def notify_git_update(self, new_version='??'):
        """Send update available message."""
        update_text = common.notifyStrings[common.NOTIFY_GIT_UPDATE_TEXT]
        title = common.notifyStrings[common.NOTIFY_GIT_UPDATE]
        self._notify_boxcar2(title, update_text + new_version)

    def notify_login(self, ipaddress=''):
        """Send the new login message."""
        update_text = common.notifyStrings[common.NOTIFY_LOGIN_TEXT]
        title = common.notifyStrings[common.NOTIFY_LOGIN]
        self._notify_boxcar2(title, update_text.format(ipaddress))

    def _notify_boxcar2(self, title, message, accesstoken=None):
        """
        Send a boxcar2 notification based on the provided info or SB config.

        title: The title of the notification to send
        message: The message string to send
        accesstoken: to send to this device
        """
        if not app.USE_BOXCAR2:
            logger.log('Notification for Boxcar2 not enabled, skipping this notification', logger.DEBUG)
            return False

        accesstoken = accesstoken or app.BOXCAR2_ACCESSTOKEN

        logger.log('Sending notification for {}'.format(message), logger.DEBUG)

        return self._send_boxcar2(message, title, accesstoken)
