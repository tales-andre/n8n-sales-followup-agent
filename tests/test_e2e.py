"""End-to-end test against the running Docker Compose stack.

    docker compose up -d
    python tests/test_e2e.py

Uses only the Python standard library plus the docker CLI. It resets the demo
data, runs the daily workflow through its webhook, approves / edits / rejects
drafts through the same form the approval page uses, and checks the database
and the Mailpit inbox after each step. It also takes the database down to check
the error alert, and (unless --quick) restarts n8n with a broken AI endpoint to
check the per-account alert. At the end n8n is restored to the demo settings.
"""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N8N = os.environ.get("N8N_URL", "http://localhost:5678")
MAILPIT = os.environ.get("MAILPIT_URL", "http://localhost:8025")
QUICK = "--quick" in sys.argv


# ---------------------------------------------------------------- helpers
def compose(*args, env=None, check=True):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, env=full_env,
                          capture_output=True, text=True, check=check)


def sql(query):
    """Run SQL in the demo database, return rows as lists of strings."""
    out = compose("exec", "-T", "postgres", "psql", "-U", "sales", "-d", "sales",
                  "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", query).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def http(method, url, data=None, timeout=90):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def run_daily():
    return http("POST", N8N + "/webhook/run-followups", data={})


def decide(fid, decision, subject="", body="", token=None):
    if token is None:
        token = sql(f"SELECT approval_token FROM followups WHERE id = {int(fid)}")[0][0]
    return http("POST", N8N + "/webhook/decision",
                {"id": fid, "token": token, "decision": decision, "subject": subject, "body": body})


def inbox():
    _, raw = http("GET", MAILPIT + "/api/v1/messages?limit=200")
    return [{"to": m["To"][0]["Address"], "subject": m["Subject"], "id": m["ID"]}
            for m in json.loads(raw)["messages"]]


def message_text(mid):
    _, raw = http("GET", MAILPIT + "/api/v1/message/" + mid)
    return json.loads(raw)["Text"]


def wait_for(predicate, timeout=60, step=1.0):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def n8n_ready():
    try:
        return http("GET", N8N + "/healthz/readiness", timeout=5)[0] == 200
    except Exception:
        return False


def restart_n8n(env=None):
    compose("up", "-d", "--force-recreate", "n8n", env=env)
    time.sleep(3)
    if not wait_for(n8n_ready, timeout=600, step=3):
        raise RuntimeError("n8n did not come back")
    # webhooks are registered a moment after the health check turns green
    wait_for(lambda: http("GET", N8N + "/webhook/approvals")[0] == 200, timeout=120, step=2)


def followup_id(company):
    return int(sql("SELECT f.id FROM followups f JOIN accounts a ON a.id = f.account_id "
                   f"WHERE a.company = '{company}'")[0][0])


def status_of(company):
    return sql("SELECT f.status FROM followups f JOIN accounts a ON a.id = f.account_id "
               f"WHERE a.company = '{company}'")[0][0]


def reset_demo_data():
    sql("TRUNCATE activity_log, followups RESTART IDENTITY; UPDATE accounts SET last_contacted_at = NULL;")
    http("DELETE", MAILPIT + "/api/v1/messages")


EXPECTED_RECONNECT = {"Harbor View Deli", "Pine Hollow Bakery", "Northgate Office Park",
                      "Bluefin Bistro", "Copper Kettle Diner"}
EXPECTED_INTRO = {"Fresh Fork Food Truck", "Corner Bean Coffee", "Hilltop Brewing Co-op"}


# ---------------------------------------------------------------- tests
class EndToEnd(unittest.TestCase):
    """Ordered scenario: test methods are named step01..stepNN and run in order."""

    @classmethod
    def setUpClass(cls):
        if not n8n_ready():
            raise unittest.SkipTest("n8n is not running on " + N8N + " - start it with `docker compose up -d`")
        reset_demo_data()

    def step01_daily_run_drafts_but_sends_nothing_to_customers(self):
        status, _ = run_daily()
        self.assertEqual(status, 200)
        rows = sql("SELECT a.company, f.kind, f.status, f.drafted_by FROM followups f "
                   "JOIN accounts a ON a.id = f.account_id")
        companies = {r[0] for r in rows}
        self.assertEqual(companies, EXPECTED_RECONNECT | EXPECTED_INTRO)
        self.assertTrue(all(r[2] == "pending_approval" for r in rows), rows)
        self.assertTrue(all(r[3] == "offline template (no AI)" for r in rows), rows)
        # quiet customer who asked not to be contacted is skipped; active customers too
        self.assertNotIn("Old Mill Tavern", companies)
        self.assertNotIn("Maple Street Cafe", companies)
        mail = wait_for(inbox, timeout=20)
        self.assertEqual(len(mail), 1, mail)       # only the summary to the manager
        self.assertEqual(mail[0]["to"], "manager@cedarridge-supply.example")
        self.assertIn("8 sales follow-up(s) waiting for your approval", mail[0]["subject"])

    def step02_second_run_same_day_drafts_nothing_new(self):
        status, _ = run_daily()
        self.assertEqual(status, 200)
        self.assertEqual(sql("SELECT count(*) FROM followups")[0][0], "8")
        self.assertEqual(len(inbox()), 1)

    def step03_approval_page_lists_every_draft(self):
        status, html = http("GET", N8N + "/webhook/approvals")
        self.assertEqual(status, 200)
        for company in EXPECTED_RECONNECT | EXPECTED_INTRO:
            self.assertIn(company, html)
        self.assertIn("Approve &amp; send", html)

    def step04_approve_with_edit_sends_the_edited_email(self):
        fid = followup_id("Harbor View Deli")
        status, html = decide(fid, "approve", subject="Quick check-in from Cedar Ridge",
                              body="Hi Sam,\n\nEdited by the sales manager, with a comma.\n\nMaria")
        self.assertEqual(status, 200)
        self.assertIn("<h1>Sent</h1>", html)
        self.assertEqual(status_of("Harbor View Deli"), "sent")
        mail = wait_for(lambda: [m for m in inbox() if m["to"] == "sam@harborviewdeli.example"], timeout=20)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail[0]["subject"], "Quick check-in from Cedar Ridge")
        self.assertIn("Edited by the sales manager, with a comma.", message_text(mail[0]["id"]))
        log = sql(f"SELECT event, detail FROM activity_log WHERE followup_id = {fid} ORDER BY id")
        self.assertEqual([e for e, _ in log], ["drafted", "approved", "sent"])
        self.assertIn("edited before approval", log[1][1])

    def step05_double_click_never_sends_twice(self):
        fid = followup_id("Harbor View Deli")
        status, html = decide(fid, "approve")
        self.assertEqual(status, 200)
        self.assertIn("Already handled", html)
        time.sleep(2)
        self.assertEqual(len([m for m in inbox() if m["to"] == "sam@harborviewdeli.example"]), 1)

    def step06_reject_sends_nothing(self):
        before = len(inbox())
        status, html = decide(followup_id("Pine Hollow Bakery"), "reject")
        self.assertEqual(status, 200)
        self.assertIn("Rejected", html)
        self.assertEqual(status_of("Pine Hollow Bakery"), "rejected")
        time.sleep(2)
        self.assertEqual(len(inbox()), before)

    def step07_wrong_token_changes_nothing(self):
        status, html = decide(followup_id("Northgate Office Park"), "approve", token="not-the-token")
        self.assertEqual(status, 200)
        self.assertIn("Already handled", html)
        self.assertEqual(status_of("Northgate Office Park"), "pending_approval")

    def step08_bad_address_is_not_sent_and_alert_names_the_account(self):
        status, html = decide(followup_id("Bluefin Bistro"), "approve")
        self.assertEqual(status, 200)
        self.assertIn("Not sent", html)
        self.assertEqual(status_of("Bluefin Bistro"), "send_failed")
        alert = wait_for(lambda: [m for m in inbox() if m["to"] == "alerts@cedarridge-supply.example"], timeout=20)
        self.assertEqual(len(alert), 1)
        self.assertIn("Bluefin Bistro", alert[0]["subject"])
        self.assertFalse([m for m in inbox() if "bluefinbistro" in m["to"]])

    def step09_database_down_sends_an_error_alert(self):
        compose("stop", "postgres")
        try:
            status, _ = run_daily()
            self.assertNotEqual(status, 200)
            alert = wait_for(lambda: [m for m in inbox() if "daily drafts failed" in m["subject"]], timeout=60)
            self.assertEqual(len(alert), 1, inbox())
            text = message_text(alert[0]["id"])
            self.assertIn("Find quiet customers and new prospects", text)
            self.assertIn("Failed run:", text)
        finally:
            compose("start", "postgres")
            wait_for(lambda: compose("exec", "-T", "postgres", "pg_isready", "-U", "sales",
                                     check=False).returncode == 0, timeout=60)

    def step10_broken_ai_endpoint_marks_drafts_failed_and_alerts_per_account(self):
        if QUICK:
            self.skipTest("--quick: skipping the n8n restart with a broken AI endpoint")
        reset_demo_data()
        try:
            # A real provider path (OpenAI-compatible), pointed at a URL that returns 404.
            # This is how a wrong URL or a rejected API key behaves: the HTTP call fails.
            restart_n8n(env={"LLM_PROVIDER": "openai", "LLM_MODEL": "any-model",
                             "LLM_BASE_URL": "http://mailpit:8025/no-such-ai-api"})
            status, _ = run_daily()
            self.assertEqual(status, 200)
            rows = sql("SELECT a.company, f.status, f.error FROM followups f JOIN accounts a ON a.id = f.account_id")
            self.assertEqual({r[0] for r in rows}, EXPECTED_RECONNECT | EXPECTED_INTRO)
            self.assertTrue(all(r[1] == "draft_failed" for r in rows), rows)
            self.assertTrue(all("AI draft failed" in r[2] for r in rows), rows)
            alerts = wait_for(lambda: [m for m in inbox() if "Could not draft" in m["subject"]]
                              if len([m for m in inbox() if "Could not draft" in m["subject"]]) >= 8 else None,
                              timeout=60)
            self.assertEqual({a["subject"].split(" for ", 1)[1] for a in alerts},
                             EXPECTED_RECONNECT | EXPECTED_INTRO)
            # no summary email and nothing to customers: only alerts
            self.assertTrue(all(m["to"] == "alerts@cedarridge-supply.example" for m in inbox()))
        finally:
            restart_n8n()
        # back on the offline template, the failed drafts are retried and succeed
        status, _ = run_daily()
        self.assertEqual(status, 200)
        self.assertEqual(sql("SELECT count(*) FROM followups WHERE status = 'pending_approval'")[0][0], "8")


def load_steps():
    names = sorted(n for n in dir(EndToEnd) if n.startswith("step"))
    suite = unittest.TestSuite()
    for n in names:
        suite.addTest(EndToEnd(n))
    return suite


def load_tests(loader, tests, pattern):  # used by `python -m unittest discover`
    return load_steps()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2, failfast=True).run(load_steps())
    sys.exit(0 if result.wasSuccessful() else 1)
