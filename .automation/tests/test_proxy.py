import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from automation.model_proxy import gateway


class ProxyTests(unittest.TestCase):
    def request(self, path, endpoint, body):
        connection = http.client.HTTPConnection("localhost", timeout=3)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.connect(str(path))
        connection.request("POST", endpoint, json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        code = response.status
        response.read()
        connection.close()
        return code

    def test_proxy_refuses_other_endpoints_models_and_large_output_requests(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ANTHROPIC_API_KEY="test-only"):
            path = Path(temp) / "api.sock"
            with gateway(path, "allowed-model"), patch("urllib.request.urlopen") as outbound:
                self.assertEqual(self.request(path, "/v1/files", {"model": "allowed-model"}), 404)
                self.assertEqual(self.request(path, "/v1/messages", {"model": "other"}), 403)
                self.assertEqual(self.request(path, "/v1/messages", {"model": "allowed-model", "max_tokens": 999999}), 403)
                outbound.assert_not_called()
            self.assertFalse(path.exists())

    def test_request_cap_prevents_outbound_call(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, ANTHROPIC_API_KEY="test-only"):
            path = Path(temp) / "api.sock"
            with gateway(path, "allowed-model", max_requests=0), patch("urllib.request.urlopen") as outbound:
                self.assertEqual(self.request(path, "/v1/messages?beta=true", {"model": "allowed-model"}), 429)
                outbound.assert_not_called()
