"""Unit tests of the SonarQube token broker: python3 -m unittest charts/mcp-sonarqube/files/test_broker.py"""
import base64
import hashlib
import importlib.util
import json
import os
import random
import sys
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_spec = importlib.util.spec_from_file_location("broker", os.path.join(os.path.dirname(__file__), "broker.py"))
broker = importlib.util.module_from_spec(_spec)
sys.modules["broker"] = broker
_spec.loader.exec_module(broker)


def _prime(bits, rng):
    while True:
        p = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(pow(a, p - 1, p) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return p


def keypair(seed):
    rng = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(512, rng), _prime(512, rng)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            return p * q, e, pow(e, -1, phi)


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(claims, key, kid="k1", alg="RS256"):
    n, _, d = key
    head = b64(json.dumps({"alg": alg, "kid": kid}).encode()) + "." + b64(json.dumps(claims).encode())
    k = (n.bit_length() + 7) // 8
    t = broker.SHA256_DIGEST_INFO + hashlib.sha256(head.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return head + "." + b64(pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big"))


KEY = keypair(1)
OTHER = keypair(2)
OIDC = "https://idp.example/realms/public"
KUBE = "https://kubernetes.default.svc"


def issuers():
    out = {}
    for iss, kind, aud in ((OIDC, "oidc", []), (KUBE, "kubernetes", ["api.example"])):
        i = broker.Issuer(issuer=iss, jwks_uri="unused", kind=kind, audiences=aud)
        i.keys = {"k1": (KEY[0], KEY[1])}
        i.fetched = time.time()
        out[iss] = i
    return out


def agent_claims(**extra):
    claims = {"iss": OIDC, "exp": time.time() + 300, "azp": "sam.team-a.c1", "preferred_username": "sam.team-a.c1",
              "sub": "kc-uuid-1"}
    claims.update(extra)
    return claims


class FakeSonar(BaseHTTPRequestHandler):
    users = {}
    tokens = {}
    groups = []
    group_names = set()
    calls = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _params(self):
        url = urllib.parse.urlsplit(self.path)
        params = dict(urllib.parse.parse_qsl(url.query))
        if self.command in ("POST", "PATCH"):
            raw = self.rfile.read(int(self.headers["Content-Length"] or 0)).decode()
            if "json" in self.headers.get("Content-Type", ""):
                params["_json"] = json.loads(raw)
                params["_type"] = self.headers["Content-Type"]
            else:
                params.update(urllib.parse.parse_qsl(raw))
        return url.path, params

    def do_GET(self):
        path, p = self._params()
        FakeSonar.calls.append(("GET", path))
        auth = self.headers.get("Authorization", "")
        if path == "/api/authentication/validate":
            valid = any(auth == "Bearer " + t for ts in self.tokens.values() for t in ts.values())
            return self._reply(200, {"valid": valid})
        if auth != "Bearer admin-token":
            return self._reply(401, {})
        if path == "/api/users/search":
            return self._reply(200, {"users": [u for u in self.users.values() if p["q"] in u["login"]]})
        if path == "/api/user_tokens/search":
            return self._reply(200, {"userTokens": [{"name": n} for n in self.tokens.get(p["login"], {})]})
        if path == broker.USERS_V2:
            return self._reply(200, {"users": [u for u in self.users.values() if p["q"] in u["login"]]})
        if path == "/api/users/groups":
            return self._reply(200, {"groups": [{"name": g} for g, u in self.groups if u == p["login"]]})
        if path == "/api/user_groups/search":
            return self._reply(200, {"groups": [{"name": g} for g in self.group_names if p["q"] in g]})
        self._reply(404, {})

    def do_PATCH(self):
        path, p = self._params()
        FakeSonar.calls.append(("PATCH", path))
        if self.headers.get("Authorization") != "Bearer admin-token" or p.get("_type") != "application/merge-patch+json":
            return self._reply(401, {})
        for user in self.users.values():
            if path == broker.USERS_V2 + "/" + user["id"]:
                user.update(p["_json"])
                # SonarQube revokes the tokens of a user whose binding changed
                FakeSonar.tokens.pop(user["login"], None)
                return self._reply(200, user)
        self._reply(404, {})

    def do_POST(self):
        path, p = self._params()
        FakeSonar.calls.append(("POST", path))
        if self.headers.get("Authorization") != "Bearer admin-token":
            return self._reply(401, {})
        if path == "/api/users/create":
            FakeSonar.users[p["login"]] = {"login": p["login"], "local": p["local"] == "true",
                                           "externalProvider": "sonarqube", "id": "u-" + p["login"],
                                           "externalLogin": p["login"], "externalId": p["login"]}
            return self._reply(200, {})
        if path == broker.USERS_V2:
            j = p["_json"]
            user = {"login": j["login"], "name": j["name"], "local": j["local"], "id": "u-" + j["login"],
                    "externalProvider": "sonarqube", "externalLogin": j["login"], "externalId": j["login"]}
            FakeSonar.users[j["login"]] = user
            return self._reply(200, user)
        if path == "/api/user_groups/add_user":
            if FakeSonar.group_names and p["name"] not in FakeSonar.group_names:
                return self._reply(404, {})
            FakeSonar.groups.append((p["name"], p["login"]))
            return self._reply(204, {})
        if path == "/api/user_groups/remove_user":
            FakeSonar.groups.remove((p["name"], p["login"]))
            return self._reply(204, {})
        if path == "/api/user_groups/create":
            FakeSonar.group_names.add(p["name"])
            return self._reply(200, {"group": {"name": p["name"]}})
        if path == "/api/user_tokens/revoke":
            FakeSonar.tokens.get(p["login"], {}).pop(p["name"], None)
            return self._reply(204, {})
        if path == "/api/user_tokens/generate":
            token = "squ_%s_%d" % (p["login"], len(FakeSonar.calls))
            FakeSonar.tokens.setdefault(p["login"], {})[p["name"]] = token
            return self._reply(200, {"token": token})
        self._reply(404, {})


class VerifyTest(unittest.TestCase):
    def test_valid_agent_token(self):
        issuer, claims = broker.verify(sign(agent_claims(), KEY), issuers())
        self.assertEqual(broker.identity(issuer, claims, "c1"), "sam.team-a.c1")

    def test_kubernetes_identity(self):
        claims = {"iss": KUBE, "aud": ["api.example"], "exp": time.time() + 300,
                  "kubernetes.io": {"namespace": "team-a", "serviceaccount": {"name": "sam"}}}
        issuer, claims = broker.verify(sign(claims, KEY), issuers())
        self.assertEqual(broker.identity(issuer, claims, "c1"), "sam.team-a.c1")

    def test_wrong_audience(self):
        claims = {"iss": KUBE, "aud": ["other"], "exp": time.time() + 300}
        with self.assertRaises(broker.Denied):
            broker.verify(sign(claims, KEY), issuers())

    def test_bad_signature(self):
        with self.assertRaises(broker.Denied):
            broker.verify(sign(agent_claims(), OTHER), issuers())

    def test_tampered_claims(self):
        h, _, s = sign(agent_claims(), KEY).split(".")
        forged = b64(json.dumps(agent_claims(azp="admin.x.c1", preferred_username="admin.x.c1")).encode())
        with self.assertRaises(broker.Denied):
            broker.verify(h + "." + forged + "." + s, issuers())

    def test_expired(self):
        with self.assertRaises(broker.Denied):
            broker.verify(sign(agent_claims(exp=time.time() - 3600), KEY), issuers())

    def test_unknown_issuer(self):
        with self.assertRaises(broker.Denied):
            broker.verify(sign(agent_claims(iss="https://evil.example"), KEY), issuers())

    def test_alg_none(self):
        with self.assertRaises(broker.Denied):
            broker.verify(sign(agent_claims(), KEY, alg="none"), issuers())

    def test_keycloak_service_account_name(self):
        claims = agent_claims(preferred_username="service-account-sam.team-a.c1")
        issuer, claims = broker.verify(sign(claims, KEY), issuers())
        self.assertEqual(broker.identity(issuer, claims, "c1"), "sam.team-a.c1")

    def test_people_are_not_served(self):
        claims = agent_claims(azp="opencode", preferred_username="admin")
        issuer, claims = broker.verify(sign(claims, KEY), issuers())
        with self.assertRaises(broker.Denied) as ctx:
            broker.identity(issuer, claims, "c1")
        self.assertEqual(ctx.exception.status, 403)


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSonar)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeSonar.users, FakeSonar.tokens, FakeSonar.groups, FakeSonar.calls = {}, {}, [], []
        FakeSonar.group_names = set()
        self.b = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1",
                               allowed=r"[^.]+\.team-a\.c1", groups=["agents"])

    def test_first_use_creates_user_and_token_then_caches(self):
        login, token = self.b.credential("Bearer " + sign(agent_claims(), KEY))
        self.assertEqual(login, "sam.team-a.c1")
        self.assertFalse(FakeSonar.users[login]["local"])
        self.assertEqual(FakeSonar.groups, [("agents", login)])
        n = len(FakeSonar.calls)
        self.assertEqual(self.b.credential("Bearer " + sign(agent_claims(), KEY))[1], token)
        self.assertEqual(len(FakeSonar.calls), n)

    def test_restart_revokes_the_previous_token(self):
        _, first = self.b.credential("Bearer " + sign(agent_claims(), KEY))
        fresh = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1")
        _, second = fresh.credential("Bearer " + sign(agent_claims(), KEY))
        self.assertNotEqual(first, second)
        self.assertEqual(list(FakeSonar.tokens["sam.team-a.c1"].values()), [second])

    def test_revoked_token_is_minted_again(self):
        login, first = self.b.credential("Bearer " + sign(agent_claims(), KEY))
        FakeSonar.tokens[login] = {}
        self.b.cache[login].checked -= 1000
        self.assertNotEqual(self.b.credential("Bearer " + sign(agent_claims(), KEY))[1], first)

    def test_not_allowed_identity(self):
        claims = agent_claims(azp="sam.team-b.c1", preferred_username="sam.team-b.c1")
        with self.assertRaises(broker.Denied) as ctx:
            self.b.credential("Bearer " + sign(claims, KEY))
        self.assertEqual(ctx.exception.status, 403)

    def test_never_mints_for_a_local_account(self):
        FakeSonar.users["sam.team-a.c1"] = {"login": "sam.team-a.c1", "local": True}
        with self.assertRaises(broker.Denied):
            self.b.credential("Bearer " + sign(agent_claims(), KEY))
        self.assertNotIn(("POST", "/api/user_tokens/generate"), FakeSonar.calls)

    def test_no_bearer(self):
        with self.assertRaises(broker.Denied):
            self.b.credential("Basic abc")


class OidcFlowTest(FlowTest):
    """Identity provider oidc: the user belongs to sonar-auth-oidc, groups follow the token."""

    def setUp(self):
        super().setUp()
        FakeSonar.group_names = {"agents", "sonar-users", "team-a-existing"}
        self.b = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1",
                               allowed=r"[^.]+\.team-a\.c1", groups=["agents"], identity_provider="oidc",
                               group_prefix="team-a-")

    def token(self, groups=("team-a-admins", "team-b-admins", "other"), **extra):
        claims = agent_claims(**extra)
        if groups is not None:
            claims["groups"] = list(groups)
        return "Bearer " + sign(claims, KEY)

    def mine(self, login="sam.team-a.c1"):
        return sorted(g for g, u in FakeSonar.groups if u == login)

    def test_first_use_creates_user_and_token_then_caches(self):
        login, token = self.b.credential(self.token())
        user = FakeSonar.users[login]
        self.assertEqual((user["local"], user["externalProvider"], user["externalLogin"], user["externalId"]),
                         (False, "oidc", "sam.team-a.c1", "kc-uuid-1"))
        # tenant groups only: team-b and unprefixed groups of the token are left out
        self.assertEqual(self.mine(), ["agents", "team-a-admins"])
        self.assertIn("team-a-admins", FakeSonar.group_names)
        self.assertIn(token, FakeSonar.tokens[login].values())
        n = len(FakeSonar.calls)
        self.assertEqual(self.b.credential(self.token())[1], token)
        self.assertEqual(len(FakeSonar.calls), n)

    def test_restart_revokes_the_previous_token(self):
        _, first = self.b.credential(self.token())
        fresh = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1", identity_provider="oidc")
        _, second = fresh.credential(self.token())
        self.assertNotEqual(first, second)
        self.assertEqual(list(FakeSonar.tokens["sam.team-a.c1"].values()), [second])

    def test_oidc_user_is_refused_in_mode_sonarqube(self):
        self.b.credential(self.token())
        back = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1")
        with self.assertRaises(broker.Denied):
            back.credential(self.token())

    def test_changed_groups_sync_without_a_new_token(self):
        _, token = self.b.credential(self.token())
        FakeSonar.groups.append(("team-a-existing", "sam.team-a.c1"))
        self.assertEqual(self.b.credential(self.token(groups=["team-a-dev"]))[1], token)
        self.assertEqual(self.mine(), ["agents", "team-a-dev"])

    def test_no_groups_claim_removes_tenant_groups(self):
        self.b.credential(self.token())
        self.b.credential(self.token(groups=None))
        self.assertEqual(self.mine(), ["agents"])

    def test_without_prefix_no_group_sync(self):
        self.b.group_prefix = ""
        self.b.credential(self.token())
        self.assertEqual(self.mine(), ["agents"])
        self.assertNotIn(("POST", "/api/user_groups/create"), FakeSonar.calls)

    def test_service_account_name_is_the_external_login(self):
        login, _ = self.b.credential(self.token(preferred_username="service-account-sam.team-a.c1"))
        self.assertEqual(FakeSonar.users[login]["externalLogin"], "service-account-sam.team-a.c1")

    def test_broker_user_of_provider_sonarqube_moves_to_oidc(self):
        FakeSonar.users["sam.team-a.c1"] = {"login": "sam.team-a.c1", "local": False, "id": "u-sam.team-a.c1",
                                            "externalProvider": "sonarqube", "externalLogin": "sam.team-a.c1"}
        self.b.credential(self.token())
        self.assertEqual(FakeSonar.users["sam.team-a.c1"]["externalProvider"], "oidc")

    def test_new_subject_is_rebound(self):
        self.b.credential(self.token())
        fresh = broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1", identity_provider="oidc")
        fresh.credential("Bearer " + sign(agent_claims(sub="kc-uuid-2"), KEY))
        self.assertEqual(FakeSonar.users["sam.team-a.c1"]["externalId"], "kc-uuid-2")

    def test_never_takes_over_another_oidc_account(self):
        FakeSonar.users["sam.team-a.c1"] = {"login": "sam.team-a.c1", "local": False, "id": "u-x",
                                            "externalProvider": "oidc", "externalLogin": "someone@example.org"}
        with self.assertRaises(broker.Denied) as ctx:
            self.b.credential(self.token())
        self.assertEqual(ctx.exception.status, 403)
        self.assertNotIn(("POST", "/api/user_tokens/generate"), FakeSonar.calls)

    def test_never_takes_over_another_provider(self):
        FakeSonar.users["sam.team-a.c1"] = {"login": "sam.team-a.c1", "local": False, "id": "u-x",
                                            "externalProvider": "github", "externalLogin": "sam.team-a.c1"}
        with self.assertRaises(broker.Denied):
            self.b.credential(self.token())

    def test_kubernetes_token_keeps_groups(self):
        claims = {"iss": KUBE, "aud": ["api.example"], "exp": time.time() + 300,
                  "sub": "system:serviceaccount:team-a:sam",
                  "kubernetes.io": {"namespace": "team-a", "serviceaccount": {"name": "sam"}}}
        login, _ = self.b.credential("Bearer " + sign(claims, KEY))
        self.assertEqual(FakeSonar.users[login]["externalId"], "system:serviceaccount:team-a:sam")
        self.assertEqual(self.mine(), ["agents"])

    def test_unknown_identity_provider(self):
        with self.assertRaises(ValueError):
            broker.Broker(broker.SonarQube(self.url, "admin-token"), issuers(), "c1", identity_provider="ldap")


class TenantGroupsTest(unittest.TestCase):
    def test_comma_separated_claim(self):
        i = issuers()[OIDC]
        self.assertEqual(broker.tenant_groups(i, {"groups": "t-a, t-b,x"}, "groups", "t-"), frozenset({"t-a", "t-b"}))

    def test_invalid_names_are_dropped(self):
        i = issuers()[OIDC]
        self.assertEqual(broker.tenant_groups(i, {"groups": ["t-a", "t-b c", "t-/x", 7]}, "groups", "t-"),
                         frozenset({"t-a"}))


if __name__ == "__main__":
    unittest.main()
