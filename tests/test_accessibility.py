import unittest
from unittest.mock import patch

import browser
from browser import (
    AccessibilityNode,
    CSSParser,
    Element,
    HTMLParser,
    Tab,
    TextLayout,
    tree_to_list,
)


class FakeBrowser:
    def __init__(self):
        self.animation_requests = []
        self.raster_requests = 0
        self.focused_content = 0
        self.focused_addressbar = 0

    def request_animation_frame(self, tab, immediate=False):
        self.animation_requests.append((tab, immediate))

    def set_needs_raster_and_draw(self):
        self.raster_requests += 1

    def focus_content(self):
        self.focused_content += 1

    def focus_addressbar(self):
        self.focused_addressbar += 1


def elements(root, tag=None):
    return [
        node for node in tree_to_list(root, [])
        if isinstance(node, Element)
        and (tag is None or node.tag == tag)
    ]


class AccessibilityTests(unittest.TestCase):
    def make_tab(self, body, tab_height=180):
        tab = Tab(tab_height)
        tab.browser = FakeBrowser()
        tab.nodes = HTMLParser(
            "<html><body>" + body + "</body></html>"
        ).parse()
        tab.render()
        return tab

    def test_focus_and_dark_media_rules_are_parsed_and_applied(self):
        rules = CSSParser(
            "div:focus { outline: 3px solid red; }"
            "@media (prefers-color-scheme: dark) {"
            " div { color: white; }"
            "}"
        ).parse()
        self.assertEqual(len(rules), 2)

        tab = self.make_tab("<div tabindex='1'>content</div>")
        tab.rules.extend(rules)
        tab.set_needs_render()
        tab.render()
        div = elements(tab.nodes, "div")[0]
        self.assertEqual(div.style["color"], "black")

        tab.set_dark_mode(True)
        tab.render()
        self.assertEqual(div.style["color"], "white")
        tab.focus_element(div)
        tab.render()
        self.assertEqual(div.style["outline"], "3px solid red")

    def test_zoom_scales_layout_and_reflows_text(self):
        tab = self.make_tab(
            "<p style='font-size: 24px'>"
            "one two three four five six seven eight nine ten"
            "</p>"
        )
        original_width = tab.document.width
        original_text_width = next(
            obj.width
            for obj in tree_to_list(tab.document, [])
            if isinstance(obj, TextLayout)
        )

        tab.zoom_by(True)
        tab.render()

        self.assertLess(tab.document.width, original_width)
        zoomed_text_width = next(
            obj.width
            for obj in tree_to_list(tab.document, [])
            if isinstance(obj, TextLayout)
        )
        self.assertGreater(zoomed_text_width, original_text_width)

    def test_tab_order_honors_positive_tabindex_then_document_order(self):
        tab = self.make_tab(
            "<a tabindex='2' href='/a'>second</a>"
            "<button tabindex='1'>first</button>"
            "<div tabindex='3'>third</div>"
            "<input name='name' value=''>"
            "<div tabindex='-1'>skipped</div>"
        )
        expected = [
            elements(tab.nodes, "button")[0],
            elements(tab.nodes, "a")[0],
            elements(tab.nodes, "div")[0],
            elements(tab.nodes, "input")[0],
        ]

        for node in expected:
            tab.advance_tab()
            self.assertIs(tab.focus, node)

        tab.advance_tab()
        self.assertIsNone(tab.focus)
        self.assertEqual(tab.browser.focused_addressbar, 1)

    def test_focus_scrolls_the_focused_control_into_view(self):
        tab = self.make_tab(
            "".join("<p>spacer {}</p>".format(i) for i in range(20))
            + "<button>last button</button>",
            tab_height=100,
        )
        button = elements(tab.nodes, "button")[0]

        tab.focus_element(button)
        tab.run_animation_frame()

        self.assertGreater(tab.scroll, 0)
        self.assertLessEqual(
            tab.scroll,
            tab.document.height + 2 * browser.VSTEP * tab.zoom
            - tab.tab_height,
        )

    def test_accessibility_tree_exposes_roles_labels_hidden_content_and_bounds(self):
        tab = self.make_tab(
            "<h1>Title</h1>"
            "<a aria-label='Read more' href='/next'>visible link</a>"
            "<input aria-label='Name field' value=''>"
            "<strong role='alert'>Updated</strong>"
            "<p aria-hidden='true'>secret text</p>"
        )
        tree = AccessibilityNode(tab.nodes)
        tree.build()
        nodes = tree_to_list(tree, [])

        self.assertEqual(tree.role, "document")
        self.assertTrue(any(node.role == "heading" for node in nodes))
        self.assertTrue(any(
            node.role == "link" and node.text == "Read more"
            for node in nodes
        ))
        self.assertTrue(any(
            node.role == "textbox" and node.text == "Name field"
            for node in nodes
        ))
        self.assertTrue(any(
            node.role == "alert" and node.children[0].text == "Updated"
            for node in nodes
        ))
        self.assertFalse(any(
            "secret text" in node.text for node in nodes
        ))
        link_node = next(node for node in nodes if node.role == "link")
        self.assertTrue(link_node.bounds)

    def test_screen_reader_reports_document_focus_and_alert_updates(self):
        tab = self.make_tab(
            "<button>announce</button>"
            "<strong role='alert'>initial alert</strong>"
        )
        fake_browser = object.__new__(browser.Browser)
        fake_browser.accessibility_is_on = True
        fake_browser.active_tab = tab
        fake_browser.accessibility_tree = tab.accessibility_tree
        fake_browser.has_spoken_document = False
        fake_browser.last_spoken_focus = None
        fake_browser.spoken_alerts = {}
        fake_browser.muted = True

        with patch("builtins.print") as output:
            fake_browser.update_accessibility()
            first_output = "\n".join(
                str(call.args[0]) for call in output.call_args_list
            )
        self.assertIn("Here are the document contents:", first_output)
        self.assertIn("Alert initial alert", first_output)

        button = elements(tab.nodes, "button")[0]
        tab.focus_element(button)
        tab.render()
        fake_browser.accessibility_tree = tab.accessibility_tree
        with patch("builtins.print") as output:
            fake_browser.update_accessibility()
            focus_output = "\n".join(
                str(call.args[0]) for call in output.call_args_list
            )
        self.assertIn("Element focused: Button", focus_output)

        alert = next(
            node for node in elements(tab.nodes)
            if node.attributes.get("role") == "alert"
        )
        alert.children[0].text = "updated alert"
        tab.set_needs_render()
        tab.render()
        fake_browser.accessibility_tree = tab.accessibility_tree
        with patch("builtins.print") as output:
            fake_browser.update_accessibility()
            alert_output = "\n".join(
                str(call.args[0]) for call in output.call_args_list
            )
        self.assertIn("Alert updated: Alert updated alert", alert_output)


if __name__ == "__main__":
    unittest.main()
