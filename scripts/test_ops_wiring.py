#!/usr/bin/env python3
"""
운영 배선 회귀 테스트 (2026-11-09) — 데일리 3.5일·27회 정지에서 드러난 구멍들
① 측정층 배선 테스트가 진짜 매시간 감시 파일을 읽었다(격리 누수) → 오염된 파일을 깔아 두고도 통과해야 한다
② 자가 수리는 workflow_run 으로만 불렸는데 시계 중계가 띄운 회차엔 그 트리거가 울리지 않았다 → 직접 호출
③ 다른 워크플로(매시간 감시)의 데이터 커밋이 다음 데일리를 세웠다 → 커밋 직후 관문 미리보기
④ 시계 중계 재시도 슬롯이 15분마다 다시 쐈다 → 한 번만
실행: python scripts/test_ops_wiring.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import clock_relay as cr        # noqa: E402
import gate_preview as gp       # noqa: E402
import repair_dispatch as rd    # noqa: E402
import spacex_watch as sw       # noqa: E402

ROOT = Path(__file__).parent.parent
KST = timezone(timedelta(hours=9))


class IsolationTest(unittest.TestCase):
    def test_measure_run_ignores_real_hourly_file(self):
        """진짜 자리에 오염된 매시간 기록(예상일 도래·중요 사건)을 깔아도 측정층 배선 테스트는 통과해야 한다."""
        import test_measure_layer as tml
        poison = Path(tempfile.mkdtemp()) / "spacex_hourly.json"
        poison.write_text(json.dumps({"fired": ["X"], "due": [{"reason": "예상일 도래"}, {"reason": "중요 사건"}],
                                      "events": [{"accn": "P-1", "kind": "earnings", "grade": "중요",
                                                  "date": "2027-01-27", "title": "오염"}]}), encoding="utf-8")
        saved = sw.HOURLY_PATH
        try:
            sw.HOURLY_PATH = poison
            r = unittest.TextTestRunner(stream=open("/dev/null", "w"), verbosity=0).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(tml.RunTest))
            self.assertTrue(r.wasSuccessful(), [str(f[1])[-300:] for f in r.failures + r.errors])
            self.assertEqual(sw.HOURLY_PATH, poison)                 # 테스트가 끝나면 원래 경로로 돌려놓는다
        finally:
            sw.HOURLY_PATH = saved


class RepairCallTest(unittest.TestCase):
    def test_dispatch_needs_repo_and_token(self):
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}):
            self.assertFalse(rd.dispatch_repair("now"))

    def test_dispatch_posts_mode(self):
        sent = {}

        class R:
            status_code = 204

        def post(url, headers=None, json=None, timeout=None):
            sent.update(url=url, body=json)
            return R()
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}), \
                mock.patch("requests.post", side_effect=post):
            self.assertTrue(rd.dispatch_repair("future"))
        self.assertIn("self-repair.yml/dispatches", sent["url"])
        self.assertEqual(sent["body"]["inputs"]["mode"], "future")

    def test_gate_preview(self):
        sent, calls = [], []
        rc = gp.main("매시간 감시", runner=lambda: (False, [{"label": "test_measure_layer: 전 케이스 통과", "got": "['x']"}]),
                     send=sent.append, dispatch=calls.append)
        self.assertEqual((rc, calls), (1, ["now"]))
        self.assertIn("다음 데일리 정지 예고", sent[0])
        self.assertIn("test_measure_layer", sent[0])
        sent.clear()
        self.assertEqual(gp.main("매시간 감시", runner=lambda: (True, []), send=sent.append, dispatch=calls.append), 0)
        self.assertEqual(sent, [])


class RelayRetryOnceTest(unittest.TestCase):
    def test_retry_fires_once(self):
        def run(hm, concl):
            t = datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST).astimezone(timezone.utc)
            return {"created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "completed", "conclusion": concl}
        failed = [run("08:16", "failure")]
        self.assertEqual(cr.decide(datetime(2026, 11, 9, 9, 15, tzinfo=KST), failed)[0], "09:10")
        again = failed + [run("09:16", "failure")]
        for hm in ("09:30", "09:45", "10:00"):                       # 실측: 이 세 시각에 또 쐈다
            self.assertIsNone(cr.decide(datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST), again)[0])


def _r(hm, concl="success", mode=None, status="completed"):
    t = datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST).astimezone(timezone.utc)
    title = "Daily Universe Dominator Update" + (f" · {mode}" if mode else "")
    return {"created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status,
            "conclusion": concl if status == "completed" else None, "display_title": title}


class RelayModeTest(unittest.TestCase):
    """⑤ 11/09 02:14Z 실측: 복구 full 이 다른 세션의 quick 에 끊겼다. quick 은 수집이 아니고, 취소된 수집은 실행이 아니다."""
    def at(self, hm):
        return datetime(2026, 11, 9, *map(int, hm.split(":")), tzinfo=KST)

    def test_quick_after_slot_does_not_consume_slot(self):
        self.assertEqual(cr.decide(self.at("12:15"), [_r("12:11", mode="quick")])[0], "12:10")
        self.assertIsNone(cr.decide(self.at("12:15"), [_r("12:11", mode="full")])[0])
        self.assertIsNone(cr.decide(self.at("12:15"), [_r("12:11")])[0])          # 꼬리 없는 옛 회차 = full(보수적)

    def test_running_quick_still_waits(self):
        self.assertIsNone(cr.decide(self.at("12:15"), [_r("12:12", mode="quick", status="in_progress")])[0])

    def test_cancelled_full_refired_once_then_folds(self):
        one = [_r("12:11", "cancelled", "full"), _r("12:12", mode="quick")]
        self.assertEqual(cr.decide(self.at("12:30"), one)[0], "12:10")
        two = one + [_r("12:31", "cancelled", "full")]
        label, why = cr.decide(self.at("12:45"), two)
        self.assertIsNone(label)
        self.assertIn("접는다", why)

    def test_quick_success_does_not_satisfy_retry(self):
        runs = [_r("08:16", "failure", "full"), _r("08:40", "success", "quick")]
        self.assertEqual(cr.decide(self.at("09:15"), runs)[0], "09:10")
        self.assertIsNone(cr.decide(self.at("09:15"), [_r("08:16", "success", "full")])[0])

    def test_heartbeat_counts_only_full_success(self):
        doc = {"workflow_runs": [{"display_title": "Daily Universe Dominator Update · quick", "updated_at": "Q"},
                                 {"display_title": "Daily Universe Dominator Update · full", "updated_at": "F"}]}
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": ""}), \
                mock.patch.object(sw, "_get_json", return_value=(doc, "ok", 200)):
            self.assertEqual(sw.collect_daily_runs(None), {"at": "F", "checked": True})
        doc["workflow_runs"] = doc["workflow_runs"][:1]
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": ""}), \
                mock.patch.object(sw, "_get_json", return_value=(doc, "ok", 200)):
            self.assertEqual(sw.collect_daily_runs(None)["at"], None)            # 재빌드만 성공 = 수집 성공 기록 없음

    def test_workflow_names_mode_and_quick_never_cancels(self):
        # pyyaml 은 CI 의존성이 아니다 — 줄 단위로 본다(최상위 키는 들여쓰기 없음)
        lines = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8").splitlines()
        rn = [ln for ln in lines if ln.startswith("run-name:")]
        self.assertEqual(len(rn), 1)
        self.assertIn("Daily Universe Dominator Update · ${{ inputs.mode == 'full' && 'full' || 'quick' }}", rn[0])
        self.assertEqual(cr.QUICK_TAG, "· quick")                               # 꼬리 = '· quick' / '· full'
        i = lines.index("concurrency:")
        self.assertEqual([ln.strip() for ln in lines[i + 1:i + 3]],
                         ["group: daily-update", "cancel-in-progress: ${{ inputs.mode == 'full' }}"])


class PublishConflictTest(unittest.TestCase):
    """⑥ 엔진이 도는 사이 사람이 보류 PR 을 합치면 게시 rebase 가 충돌한다 — 예전엔 예외로 죽어 산출물이 사라졌다."""
    def _publish(self, pull_rc):
        import probe_gate as pg
        calls, dms = [], []

        class R:
            def __init__(self, rc, out=""):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        def git(*args, check=True):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return R(1)                                            # 게시할 변경이 있다
            if args[0] == "pull":
                return R(pull_rc)
            return R(0)

        def rj(path, default=None):
            return {"ok": True, "failures": []} if "gate_final" in str(path) else default

        with mock.patch.object(pg, "_git", side_effect=git), mock.patch.object(pg, "read_json", side_effect=rj), \
                mock.patch.object(pg, "send_dm", side_effect=dms.append), \
                mock.patch.object(pg, "check_private", return_value=[]), \
                mock.patch.object(pg, "summary_lines", return_value=[]), \
                mock.patch.object(pg, "prob_line", return_value=""), \
                mock.patch.object(pg, "_step_output"), \
                mock.patch.object(pg.subprocess, "run", return_value=R(0, "https://github.com/o/r/pull/9")), \
                mock.patch.dict("os.environ", {"PROBE_PUBLISH": "", "SELFTEST_OUTCOME": ""}):
            rc = pg.cmd_publish()
        return rc, calls, dms

    def test_conflict_preserves_output_on_branch(self):
        rc, calls, dms = self._publish(pull_rc=1)
        self.assertEqual(rc, 1)                                            # 실패로 보고(워크플로가 운다)
        self.assertEqual(sum(1 for c in calls if c[0] == "commit"), 1)     # 커밋은 한 번 — 두 번째 커밋으로 죽지 않는다
        self.assertTrue(any(c[:2] == ("checkout", "-B") for c in calls))
        self.assertTrue(any(c[:2] == ("push", "-f") for c in calls))       # 브랜치로 보존
        self.assertIn("main 게시 충돌", dms[-1])
        self.assertIn("pull/9", dms[-1])

    def test_clean_publish_goes_to_main(self):
        rc, calls, dms = self._publish(pull_rc=0)
        self.assertEqual(rc, 0)
        self.assertTrue(any(c[:2] == ("push", "origin") and c[2] == "HEAD:main" for c in calls))
        self.assertFalse(any(c[:2] == ("push", "-f") for c in calls))


class UnpublishedRunsTest(unittest.TestCase):
    """⑦ 관문에 막힌 회차(probe/ 브랜치)는 다음 회차가 규약으로 이어받는다 — 작성자 재량에 맡기지 않는다."""
    def setUp(self):
        import probe_gate as pg
        self.pg = pg
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (pg.WORK, pg.VLIST, pg.VRES, pg.REPORTS)
        (self.tmp / "reports").mkdir()
        (self.tmp / "reports" / "2026-11-05-event.md").write_text("x", encoding="utf-8")   # 이미 게시된 회차
        pg.WORK, pg.VLIST, pg.VRES = self.tmp / "_probe", self.tmp / "vl.json", self.tmp / "vr.json"
        pg.REPORTS = self.tmp / "reports"

    def tearDown(self):
        self.pg.WORK, self.pg.VLIST, self.pg.VRES, self.pg.REPORTS = self.saved

    def _git(self, *args, check=True):
        class R:
            def __init__(s, rc=0, out=""):
                s.returncode, s.stdout, s.stderr = rc, out, ""
        if args[0] == "ls-remote":
            return R(0, "a\trefs/heads/probe/2026-11-08-event\nb\trefs/heads/probe/2026-11-05-event\n"
                        "c\trefs/heads/probe-brk/2026-11\nd\trefs/heads/repair/now-x\n")
        if args[0] == "show":
            return R(0, "# 11/08 보고서 본문")
        return R(0)

    def test_finds_only_unpublished_and_feeds_author(self):
        pg = self.pg
        with mock.patch.object(pg, "_git", side_effect=self._git):
            self.assertEqual(pg.unpublished_branches(), [("probe/2026-11-08-event", "2026-11-08-event")])
            pg.cmd_prepare("event", "테스트")
        prompt = (pg.WORK / "author_prompt.md").read_text(encoding="utf-8")
        self.assertIn("main 에 게시되지 못한 회차", prompt)
        self.assertIn("_probe/unpublished/2026-11-08-event.md", prompt)
        self.assertIn("unpublished_runs", prompt)
        self.assertEqual((pg.WORK / "unpublished" / "2026-11-08-event.md").read_text(encoding="utf-8"), "# 11/08 보고서 본문")
        self.assertEqual(json.loads((pg.WORK / "context.json").read_text(encoding="utf-8"))["unpublished"],
                         ["2026-11-08-event"])

    def test_close_only_absorbed(self):
        pg = self.pg
        ran = []

        class R:
            returncode, stdout, stderr = 0, "", ""
        st = {"run": {"run_id": "2026-11-09-event", "unpublished_runs": [
            {"run_id": "2026-11-08-event", "absorbed_by": "2026-11-09-event — T3"},
            {"run_id": "2026-11-07-event", "not_absorbed": "원천 재확인 실패"}]}}
        with mock.patch.object(pg.subprocess, "run", side_effect=lambda a, **k: ran.append(a) or R()):
            self.assertEqual(pg.close_absorbed(st), ["2026-11-08-event"])
        self.assertEqual([a[:4] for a in ran], [["gh", "pr", "close", "probe/2026-11-08-event"]])


class WorkflowWiringTest(unittest.TestCase):
    def _block(self, y, needle):
        i = y.index(needle)
        return y[y.rindex("- name:", 0, i):y.index("- name:", i)] if "- name:" in y[i:] else y[y.rindex("- name:", 0, i):]

    def test_daily_calls_repair_directly(self):
        y = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        b = self._block(y, "python scripts/repair_dispatch.py now")
        self.assertIn("if: failure() && steps.selftest.outcome == 'failure'", b)
        self.assertIn("actions: write", y)
        self.assertLess(y.index("repair_dispatch.py now"), y.index("Hard failure alarm"))
        self.assertIn("GITHUB_TOKEN", self._block(y, "python scripts/timebomb_probe.py"))

    def test_daily_checks_out_tip_and_fails_on_lost_push(self):
        """quick 이 full 뒤에서 기다렸다 시작해도 최신 끝을 받는다 · push 3회 거부는 실패로 끝난다."""
        y = (ROOT / ".github" / "workflows" / "daily-update.yml").read_text(encoding="utf-8")
        co = self._block(y, "uses: actions/checkout@")
        self.assertIn("ref: ${{ github.ref }}", co)
        cm = self._block(y, 'git commit -m "Auto update:')
        self.assertIn("pushed=1", cm)
        self.assertIn('if [ "$pushed" != "1" ]; then', cm)
        self.assertIn("exit 1", cm[cm.index('if [ "$pushed" != "1" ]'):])

    def test_watch_previews_gate_after_commit(self):
        y = (ROOT / ".github" / "workflows" / "spacex-watch.yml").read_text(encoding="utf-8")
        self.assertIn('echo "committed=true" >> "$GITHUB_OUTPUT"', y)
        b = self._block(y, "python scripts/gate_preview.py")
        self.assertIn("if: steps.commit.outputs.committed == 'true'", b)
        self.assertIn("continue-on-error: true", b)
        self.assertNotIn("TELEGRAM_CHAT_ID:", b)                      # 소장 DM 전용
        self.assertLess(y.index("id: commit"), y.index("gate_preview.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
