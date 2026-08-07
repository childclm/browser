import unittest
from unittest.mock import patch

import browser
from browser import Element, JSContext, Tab, Task, TaskRunner, URL, tree_to_list


class FakeBrowser:
    def __init__(self):
        self.animation_requests = []
        self.raster_requests = 0

    def request_animation_frame(self, tab, immediate=False):
        self.animation_requests.append((tab, immediate))

    def set_needs_raster_and_draw(self):
        self.raster_requests += 1


class ImmediateTimer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback

    def start(self):
        self.callback()


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class SchedulingTests(unittest.TestCase):
    def make_context(self):
        tab = Tab(600)
        tab.browser = FakeBrowser()
        tab.url = URL("http://example.com/page")
        return tab, JSContext(tab)

    def test_external_scripts_are_queued_until_the_event_loop_runs_them(self):
        tab = Tab(600)
        tab.browser = FakeBrowser()

        responses = {
            "/page": (
                {},
                "<html><body><strong>not run</strong>"
                "<script src='/script.js'></script></body></html>",
            ),
            "/script.js": (
                {},
                'document.querySelectorAll("strong")[0].innerHTML = "ran";',
            ),
        }

        def fake_request(url, referrer=None, payload=None):
            return responses[url.path]

        with patch.object(browser.URL, "request", fake_request):
            tab.load(URL("http://example.com/page"))

        strong = next(
            node
            for node in tree_to_list(tab.nodes, [])
            if isinstance(node, Element) and node.tag == "strong"
        )
        self.assertEqual(strong.children[0].text, "not run")
        self.assertTrue(tab.task_runner.run_one())
        self.assertTrue(tab.needs_render)
        tab.render()
        self.assertEqual(strong.children[0].text, "ran")

    def test_task_runner_is_fifo_and_can_run_one_task(self):
        runner = TaskRunner(None)
        calls = []
        runner.schedule_task(Task(calls.append, "first"))
        runner.schedule_task(Task(calls.append, "second"))

        self.assertTrue(runner.run_one())
        self.assertEqual(calls, ["first"])
        self.assertEqual(runner.run_tasks(), 1)
        self.assertEqual(calls, ["first", "second"])
        self.assertFalse(runner.run_one())

    def test_set_timeout_queues_callback_until_task_runner_runs(self):
        tab, ctx = self.make_context()

        with patch.object(browser.threading, "Timer", ImmediateTimer):
            ctx.run(
                "timer.js",
                """
                var calls = 0;
                setTimeout(function() {
                    calls = calls + 1;
                }, 20);
                """,
            )

        self.assertEqual(ctx.interp.evaljs("calls"), 0)
        self.assertTrue(tab.task_runner.run_one())
        self.assertEqual(ctx.interp.evaljs("calls"), 1)

    def test_request_animation_frame_drains_current_batch_before_next_frame(self):
        tab, ctx = self.make_context()

        ctx.run(
            "raf.js",
            """
            var calls = 0;
            function frame() {
                calls = calls + 1;
                if (calls < 2) requestAnimationFrame(frame);
            }
            requestAnimationFrame(frame);
            """,
        )

        self.assertEqual(len(tab.browser.animation_requests), 1)
        ctx.interp.evaljs("__runRAFHandlers()")
        self.assertEqual(ctx.interp.evaljs("calls"), 1)
        self.assertEqual(len(tab.browser.animation_requests), 2)

        ctx.interp.evaljs("__runRAFHandlers()")
        self.assertEqual(ctx.interp.evaljs("calls"), 2)

    def test_async_xhr_schedules_onload_on_tab_queue(self):
        tab, ctx = self.make_context()

        def fake_request(url, referrer=None, payload=None):
            return {}, "async response"

        with patch.object(browser.URL, "request", fake_request):
            with patch.object(browser.threading, "Thread", ImmediateThread):
                ctx.run(
                    "xhr.js",
                    """
                    var result = "waiting";
                    var xhr = new XMLHttpRequest();
                    xhr.open("GET", "/data", true);
                    xhr.onload = function() {
                        result = this.responseText;
                    };
                    xhr.send();
                    """,
                )

        self.assertEqual(ctx.interp.evaljs("result"), "waiting")
        self.assertTrue(tab.task_runner.run_one())
        self.assertEqual(ctx.interp.evaljs("result"), "async response")


if __name__ == "__main__":
    unittest.main()
