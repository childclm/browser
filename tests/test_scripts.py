import unittest

import server
from browser import Element, HTMLParser, JSContext, Tab, tree_to_list


class FakeTab:
    def __init__(self, html):
        self.nodes = HTMLParser(html).parse()
        self.render_count = 0

    def render(self):
        self.render_count += 1


def first_element(root, tag):
    for node in tree_to_list(root, []):
        if isinstance(node, Element) and node.tag == tag:
            return node
    raise AssertionError(f"No <{tag}> element found")


class ScriptSupportTests(unittest.TestCase):
    def test_dom_methods_wrap_handles_and_set_inner_html(self):
        tab = FakeTab("<html><body><p id='message'>old</p></body></html>")
        ctx = JSContext(tab)

        ctx.run(
            "dom.js",
            """
            var p = document.querySelectorAll("p")[0];
            if (p.getAttribute("id") == "message") {
                p.innerHTML = "<b>new</b>";
            }
            """,
        )

        paragraph = first_element(tab.nodes, "p")
        self.assertEqual(tab.render_count, 1)
        self.assertEqual(len(paragraph.children), 1)
        self.assertIsInstance(paragraph.children[0], Element)
        self.assertEqual(paragraph.children[0].tag, "b")
        self.assertIs(paragraph.children[0].parent, paragraph)
        self.assertEqual(paragraph.children[0].children[0].text, "new")

    def test_event_dispatch_exposes_event_object_and_prevents_default(self):
        tab = FakeTab("<html><body><input name='guest' value='abc'></body></html>")
        ctx = JSContext(tab)
        input_node = first_element(tab.nodes, "input")

        ctx.run(
            "events.js",
            """
            var input = document.querySelectorAll("input")[0];
            var keydownCalls = 0;
            input.addEventListener("keydown", function(e) {
                keydownCalls = keydownCalls + 1;
                if (e.type == "keydown" && this.getAttribute("name") == "guest") {
                    e.preventDefault();
                }
            });
            """,
        )

        self.assertTrue(ctx.dispatch_event("keydown", input_node))
        self.assertEqual(ctx.interp.evaljs("keydownCalls"), 1)
        self.assertFalse(
            ctx.interp.evaljs(
                'document.querySelectorAll("input")[0].dispatchEvent(new Event("keydown"))'
            )
        )
        self.assertEqual(ctx.interp.evaljs("keydownCalls"), 2)

    def test_keypress_respects_prevented_keydown_event(self):
        tab = Tab(600)
        tab.nodes = HTMLParser("<html><body><input value=''></body></html>").parse()
        input_node = first_element(tab.nodes, "input")
        tab.focus = input_node
        tab.js = JSContext(tab)
        renders = []
        tab.render = lambda: renders.append(True)

        tab.js.run(
            "prevent-keydown.js",
            """
            document.querySelectorAll("input")[0].addEventListener("keydown", function(e) {
                e.preventDefault();
            });
            """,
        )

        tab.keypress("x")

        self.assertEqual(input_node.attributes.get("value", ""), "")
        self.assertEqual(renders, [])

    def test_click_event_can_prevent_input_focus(self):
        tab = Tab(600)
        tab.nodes = HTMLParser("<html><body><input value=''></body></html>").parse()
        input_node = first_element(tab.nodes, "input")
        tab.focus = None
        tab.js = JSContext(tab)
        tab.hit_test = lambda x, y: input_node

        tab.js.run(
            "prevent-click.js",
            """
            document.querySelectorAll("input")[0].addEventListener("click", function(e) {
                e.preventDefault();
            });
            """,
        )

        tab.click(0, 0)

        self.assertIsNone(tab.focus)
        self.assertFalse(input_node.is_focused)

    def test_tutorial_guest_book_script_updates_warning_and_blocks_submit(self):
        tab = FakeTab(server.show_comments({"user": "crashoverride"}))
        ctx = JSContext(tab)
        input_node = first_element(tab.nodes, "input")
        form_node = first_element(tab.nodes, "form")
        warning_node = first_element(tab.nodes, "strong")

        ctx.run("/comment.js", server.comment_js())

        input_node.attributes["value"] = "x" * 101
        self.assertFalse(ctx.dispatch_event("keydown", input_node))
        self.assertEqual(warning_node.children[0].text, "Comment too long!")
        self.assertTrue(ctx.dispatch_event("submit", form_node))

        input_node.attributes["value"] = "short"
        self.assertFalse(ctx.dispatch_event("keydown", input_node))
        self.assertEqual(warning_node.children, [])
        self.assertFalse(ctx.dispatch_event("submit", form_node))


if __name__ == "__main__":
    unittest.main()
