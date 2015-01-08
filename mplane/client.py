#
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
##
# mPlane Protocol Reference Implementation
# Client SDK API implementation
#
# (c) 2013-2014 mPlane Consortium (http://www.ict-mplane.eu)
#               Author: Brian Trammell <brian@trammell.ch>
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

import mplane.model
import mplane.utils
import sys

import html.parser
import urllib3
import os.path

import tornado.web
import tornado.httpserver
import tornado.ioloop

from datetime import datetime, timedelta

CAPABILITY_PATH_ELEM = "capability"

FORGED_DN_HEADER = "Forged-MPlane-Identity"
DEFAULT_IDENTITY = "default"

class BaseClient(object):
    """
    Core implementation of a generic programmatic client.
    Used for common client state management between 
    Client and ClientListener; use one of these instead.

    """

    def __init__(self, tls_state=None):
        self._tls_state = tls_state
        self._capabilities = {}
        self._capability_labels = {}
        self._capability_identities = {}
        self._receipts = {}
        self._receipt_labels = {}
        self._results = {}
        self._result_labels = {}       

    def _add_capability(self, msg, identity):
        """
        Add a capability to internal state. The capability will be recallable 
        by token, and, if present, by label.

        Internal use only; use handle_message instead.

        """

        # FIXME retoken on token collision with another identity
        token = msg.get_token()

        self._capabilities[token] = msg

        if msg.get_label():
            self._capability_labels[msg.get_label()] = msg

        if identity:
            self._capability_identities[token] = identity

    def _remove_capability(self, msg):
        token = msg.get_token()
        if token in self._capabilities:
            label = self._capabilities[token].get_label()
            del self._capabilities[token]
            if label and label in self._capability_labels:
                del self._capability_labels[label]

    def _withdraw_capability(self, msg, identity):
        """
        Process a withdrawal. Match the withdrawal to the capability, 
        first by token, then by schema. Withdrawals that do not match
        any known capabilities are dropped silently.

        Internal use only; use handle_message instead.

        """
        token = msg.get_token()

        # FIXME check identity, exception on mismatch

        if token in self._capabilities:
            self._remove_capability(self._capabilities[token])
        else:
            # Search all capabilities by schema
            for cap in self.capabilities_matching_schema(msg):
                self._remove_capability(cap.get_token())

    def capability_for(self, token_or_label):
        """
        Retrieve a capability given a token or label.

        """
        if token_or_label in self._capability_labels:
            return self._capability_labels[token_or_label]
        elif token_or_label in self._capabilities:
            return self._capabilities[token_or_label]
        else:
            raise KeyError("no capability for token or label "+token_or_label)

    def identity_for(self, token_or_label):
        """
        Retrieve an identity given a capability token or label.

        """
        if token_or_label in self._capability_identities:
            return self._capability_identities[token_or_label]
        elif token_or_label in self._capability_labels:
            return self._capability_identities[self._capability_labels[token_or_label].get_token()]
        else:
            raise KeyError("no identity for capability token or label "+token_or_label)

    def capabilities_matching_schema(self, schema_capability):
        """
        Given a capability, return *all* known capabilities matching the 
        given schema capability. A capability matches a schema capability 
        if and only if: (1) the capability schemas match and (2) all
        constraints in the capability are contained by all constraints
        in the schema capability.

        Used to programmatically select capabilities matching an
        aggregation or other collection operation (e.g. at a supervisor).

        """
        # FIXME write this, maybe refactor part back into model.
        pass

    def _spec_for(self, cap_tol, when, params, relabel=None):
        """
        Given a capability token or label, a temporal scope, a dictionary 
        of parameters, and an optional new label, derive a specification
        ready for invocation, and return the capability and specification.

        Used internally by derived classes; use invoke_capability instead.

        """
        cap = self.capability_for(cap_tol)
        spec = mplane.model.Specification(capability=cap)

        # set temporal scope
        spec.set_when(when)

        # fill in parameters
        spec.set_single_values()
        for pname in spec.parameter_names():
            if spec.get_parameter_value(pname) is None:
                if pname in params:
                    spec.set_parameter_value(pname, params[pname])
                else:
                    raise KeyError("missing parameter "+pname)

        # regenerate token based on parameters and temporal scope
        spec.retoken()

        # generate label
        if relabel:
            spec.set_label(relabel)
        else:
            spec.set_label(cap.get_label() + "-" + str(self._ssn))
        self._ssn += 1

        return (cap, spec)

    def _handle_receipt(self, msg, identity):
        self._add_receipt(msg)

    def _add_receipt(self, msg):
        """
        Add a receipt to internal state. The receipt will be recallable 
        by token, and, if present, by label.

        Internal use only; use handle_message instead.

        """
        self._receipts[msg.get_token()] = msg
        if msg.get_label():
            self._receipt_labels[msg.get_label()] = msg

    def _remove_receipt(self, msg):
        token = msg.get_token()
        if token in self._receipts:
            label = self._receipts[token].get_label()
            del self._receipts[token]
            if label and label in self._receipt_labels:
                del self._receipt_labels[label]

    def _handle_result(self, msg, identity):
        # FIXME check the result identity 
        # against where we sent the specification to
        self._add_result(msg)

    def _add_result(self, msg):
        """
        Add a result to internal state. The result will supercede any receipt
        stored for the same token, and will be recallable by token, and, 
        if present, by label.

        Internal use only; use handle_message instead.

        """        
        try:
            receipt = self._receipts[msg.get_token()]
            self._remove_receipt(receipt)
        except KeyError:
            pass
        self._results[msg.get_token()] = msg

        if msg.get_label():
            self._result_labels[msg.get_label()] = msg

    def _remove_result(self, msg):
        token = msg.get_token()
        if token in self._results:
            label = self._results[token].get_label()
            del self._results[token]
            if label and label in self._result_labels:
                del self._result_labels[label]

    def result_for(self, token_or_label):
        """
        return a result for the token if available;
        return the receipt for the token otherwise.
        """
        # first look in state
        if token_or_label in self._result_labels:
            return self._result_labels[token_or_label]
        elif token_or_label in self._results:
            return self._results[token_or_label]
        elif token_or_label in self._receipt_labels:
            receipt = self._receipt_labels[token_or_label]
        elif token_or_label in self._receipts:
            receipt = self._receipts[token_or_label]
        else:
            raise KeyError("no such token or label "+token_or_label)
       
    def _handle_exception(self, msg):
        # FIXME what do we do with these?
        pass

    def handle_message(self, msg, identity=None):
        """
        Handle a message. Used internally to process 
        mPlane messages received from a component. Can also be used 
        to inject messages into a client's state.

        """

        if isinstance(msg, mplane.model.Capability):
            self._add_capability(msg, identity)
        elif isinstance(msg, mplane.model.Withdrawal):
            self._withdraw_capability(msg, identity)
        elif isinstance(msg, mplane.model.Receipt):
            self._handle_receipt(msg, identity)
        elif isinstance(msg, mplane.model.Result):
            self._handle_result(msg, identity)
        elif isinstance(msg, mplane.model.Exception):
            self._handle_exception(msg, identity)
        elif isinstance(msg, mplane.model.Envelope):
            for imsg in msg.messages():
                self.handle_message(imsg, identity)
        else:
            raise ValueError("Internal error: unknown message "+repr(msg))

    def forget(self, token_or_label):
        """
        forget all receipts and results for the given token or label
        """
        if token_or_label in self._result_labels:
            result = self._result_labels[token_or_label]
            del self._result_labels[token_or_label]
            del self._results[result.get_token()]

        if token_or_label in self._results:
            result = self._results[token_or_label]
            del self._results[token_or_label]
            if result.get_label():
                del self._result_labels[result.get_label()]

        if token_or_label in self._receipt_labels:
            receipt = self._receipt_labels[token_or_label]
            del self._receipt_labels[token_or_label]
            del self._receipts[receipt.get_token()]

        if token_or_label in self._receipts:
            receipt = self._receipts[token_or_label]
            del self._receipts[token_or_label]
            if receipt.get_label():
                del self._receipt_labels[receipt.get_label()]

    def receipt_tokens(self):
        """
        list all tokens for outstanding receipts
        """
        return tuple(self._receipts.keys())

    def receipt_labels(self):
        """
        list all labels for outstanding receipts
        """
        return tuple(self._receipt_labels.keys())

    def result_tokens(self):
        """
        list all tokens for stored results
        """
        return tuple(self._results.keys())

    def result_labels(self):
        """
        list all labels for stored results
        """
        return tuple(self._result_labels.keys())

    def capability_tokens(self):
        """
        list all tokens for stored capabilities
        """
        return tuple(self._capabilities.keys())

    def capability_labels(self):
        """
        list all labels for stored capabilities
        """
        return tuple(self._capability_labels.keys())

class CrawlParser(html.parser.HTMLParser):
    """
    HTML parser class to extract all URLS in a href attributes in
    an HTML page. Used to extract links to Capabilities exposed
    as link collections.

    """
    def __init__(self, **kwargs):
        super(CrawlParser, self).__init__(**kwargs)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = {k: v for (k,v) in attrs}
        if tag == "a" and "href" in attrs:
            self.urls.append(attrs["href"])

class HttpClient(BaseClient):
    """
    Core implementation of an mPlane JSON-over-HTTP(S) client.
    Supports client-initiated workflows. Intended for building 
    client UIs and bots.

    """

    def __init__(self, tls_state=None, default_url=None):
        """
        initialize a client with a given 
        default URL an a given TLS state
        """
        super().__init__(tls_state)
        
        self._default_url = default_url

        # specification serial number
        # used to create labels programmatically
        self._ssn = 0

    def set_default_url(self, url):
        self._default_url = url

    def send_message(self, msg, dst_url=None):
        """
        send a message, store any result in client state.
        
        """

        # figure out where to send the message
        if not dst_url:
            dst_url = self._default_url

        pool = self.tls_state.pool_for(dst_url)
        res = pool.urlopen('POST', dst_url, 
                           body=mplane.model.unparse_json(msg).encode("utf-8"),
                           headers={"content-type": "application/x-mplane+json"})
        if (res.status == 200 and 
            res.getheader("content-type") == "application/x-mplane+json"):
            self.handle_message(mplane.model.parse_json(res.data.decode("utf-8")))
        else:
            # Didn't get an mPlane reply. What now?
            pass

    def result_for(self, token_or_label):
        """
        return a result for the token if available;
        attempt to redeem the receipt for the token otherwise;
        if not yet redeemable, return the receipt instead.
        """
        # go get a raw receipt or result
        rr = super().result_for(token_or_label)
        if isinstance(rr, mplane.model.Result):
            return rr

        # if we're here, we have a receipt. try to redeem it.
        self.send_message(mplane.model.Redemption(receipt=rr))

        # see if we got a result
        if token_or_label in self._result_labels:
            return self._result_labels[token_or_label]
        elif token_or_label in self._results:
            return self._results[token_or_label]
        else:
            # Nope. Return the receipt.
            return rr

    def invoke_capability(self, cap_tol, when, params, relabel=None):
        """
        Given a capability token or label, a temporal scope, a dictionary 
        of parameters, and an optional new label, derive a specification
        and send it to the appropriate destination.

        """
        (cap, spec) = self._spec_for(cap_tol, when, params, relabel)
        spec.validate()
        self.send_message(spec, dst_url=cap.get_link())

    def retrieve_capabilities(self, url, urlchain=[]):
        """
        connect to the given URL, retrieve and process the 
        capabilities/withdrawals found there
        """
        # detect loops in capability links
        if url in urlchain:
            return

        pool = self.tls_state.pool_for(dst_url)
        res = pool.request('get', dst_url)

        if res.status == 200:
            ctype = res.getheader("content-type")
            if ctype == "application/x-mplane+json":
                # Probably an envelope. Process the message.
                self.handle_message(
                    mplane.model.parse_json(res.data.decode("utf-8")))
            elif ctype == "text/html":
                # Treat as a list of links to capability messages.
                parser = CrawlParser(strict=False)
                parser.feed(res.data.decode("utf-8"))
                parser.close()
                for capurl in parser.urls:
                    self.retrieve_capabilities(url=capurl, 
                                               urlchain=urlchain + [url])

class HttpListenerClient(BaseClient):
    """
    Core implementation of an mPlane JSON-over-HTTP(S) client.
    Supports component-initiated workflows. Intended for building 
    supervisors.

    """
    def __init__(self, tls_state=None, listen_addr=None, path=None):
        super().__init__(tls_state)

        # Outgoing messages per component identifier
        self._outgoing = {}

        # Capability 
        self._callback_capability = {}

        # Create a request handler pointing at this client
        self._tornado_application = tornado.web.Application([
            (r"/", ListenerClientHandler, {'listenerclient': self})])

    def listen_on(self, host, port):
        """
        start a thread to listen on a given host and port. 
        This will asynchronously serve components accessing the client.
        """
        # FIXME this is straight from the example code, 
        # having more control over this might be nice.
        tornado.httpserver.HTTPServer(self._tornado_application)
        tornado.ioloop.IOLoop.instance().start()

    def _push_outgoing(self, identity, msg):
        if identity not in self._outgoing:
            self._outgoing[identity] = []
        self._outgoing[identity].append(msg)

    def invoke_capability(self, cap_tol, when, params, relabel=None, callback_when=None):
        """
        Given a capability token or label, a temporal scope, a dictionary 
        of parameters, and an optional new label, derive a specification
        and queue it for retrieval by the appropriate identity (i.e., the
        one associated with the capability).

        If the identity has indicated it supports callback control,
        the optional callback_when parameter queues a callback spec to
        schedule the next callback.
        """
        # grab cap, spec, and identity
        (cap, spec) = self._spec_for(cap_tol, when, params, relabel)
        identity = self.identity_for(cap.get_token())

        # prepare a callback spec if we need to
        callback_cap = self._callback_capability[identity] 
        if callback_cap and callback_when:
            callback_spec = mplane.model.Specification(capability=callback_cap)
            callback_spec.set_when(callback_when)
            envelope = mplane.model.Envelope()
            envelope.append_message(callback_spec)
            envelope.append_message(spec)
            self._push_outgoing(identity, envelope)
        else:
            self._push_outgoing(identity, spec)

    def _add_capability(self, msg, identity):
        """
        Override Client's add_capability, check for callback control
        """
        if msg.get_verb() == mplane.model.VERB_CALLBACK:
            # FIXME this is kind of dodgy; we should do better checks to
            # make sure this is a real callback capability
            self._callback_capability[identity] = msg
        else:
            # not a callback control cap, just add the capability
            super().add_capability(msg, identity)

class ListenerClientHandler(tornado.web.RequestHandler):
    def initialize(self, listenerclient):
        self._client = client

    def post(self):
        # unwrap json message from body
        if (self.request.headers["Content-Type"] == "application/x-mplane+json"):
            msg = mplane.model.parse_json(self.request.body.decode("utf-8"))
        else:
            # FIXME how do we tell tornado we don't want to handle this?
            raise ValueError("I only know how to handle mPlane JSON messages via HTTP POST")

        # figure out who is posting this
        if False:
            # FIXME get the identity from the DN here
            pass
        elif FORGED_DN_HEADER in self.request.headers:
            identity = self.request.headers[FORGED_DN_HEADER]
        else:
            identity = DEFAULT_IDENTITY

        # now handle the message
        self._client.handle_message(msg, identity)

