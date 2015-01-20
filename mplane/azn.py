#
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
##
# mPlane Protocol Reference Implementation
# Authorization context for mPlane components
#
# (c) 2014-2015 mPlane Consortium (http://www.ict-mplane.eu)
#     Author: Stefano Pentassuglia <stefano.pentassuglia@ssbprogetti.it>
#
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.
#

# FIXME grab most of this from sec.py, which then goes away.

import mplane.model
import configparser

# Factory function to create Authorization ON or OFF object
def Authorization(config_file = None):
    if config_file is None:
        return AuthorizationOff()
    else:
        return AuthorizationOn(config_file)
        
class AuthorizationOff(object):
        
    def check(self, cap, identity):
        return True

always_authorized = AuthorizationOff()

class AuthorizationOn(object):
    
    def __init__(self, config_file):
        config = configparser.ConfigParser()
        config.optionxform = str
        config.read(config_file)
        self.id_role = self._load_roles(config["Roles"])
        self.cap_role = self._load_roles(config["Authorizations"])

    def _load_roles(self, config_obj):
        """ Loads user-role-capability associations and keeps them in cache """
        r = {}
        for elem in config_obj:
            roles = set(config_obj[elem].split(','))
            r[elem] = roles
        return r
            
    def check(self, cap, identity): 
        """
        Return true if the given identiy is authorized to use the given
        capability by this set of authorization rules, false otherwise.

        """
        cap_label = cap._label
        if ((cap_label in self.cap_role) and (identity in self.id_role)): # Deny unless explicitly allowed in .conf files
            intersection = self.cap_role[cap_label] & self.id_role[identity]
            if len(intersection) > 0:
                return True
        return False