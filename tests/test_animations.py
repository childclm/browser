import unittest

import browser
from browser import (
    CSSParser,
    Element,
    HTMLParser,
    JSContext,
    Tab,
    Transform,
    tree_to_list,
)


class FakeBrowser:
    def __init__(self):
        self.animation_requests = []
        self.raster_requests = 0

    def request_animation_frame(self, tab, immediate=False):
        self.animation_requests.append((tab, immediate))

    def set_needs_raster_and_draw(self):
        self.raster_requests += 1


def first_element(root, tag):
    for node in tree_to_list(root, []):
        if isinstance(node, Element) and node.tag == tag:
            return node
    raise AssertionError(f"No <{tag}> element found")


class AnimationTests(unittest.TestCase):
    def make_tab(self):
        tab = Tab(500)
        tab.browser = FakeBrowser()
        tab.nodes = HTMLParser(
            "<html><body>"
            "<div style='opacity:0.1;transform:translate(0px, 0px);"
            "transition:opacity 0.2s, transform 0.2s'>box</div>"
            "</body></html>"
        ).parse()
        tab.needs_render = True
        tab.needs_style = True
        tab.needs_layout = True
        tab.needs_paint = True
        tab.render()
        return tab

    def test_css_parser_keeps_function_values_and_transition_lists(self):
        pairs = CSSParser(
            "transition: opacity 200ms, transform 0.2s;"
            "transform: translate(20px, 5px);"
        ).body()

        self.assertEqual(
            pairs["transition"], "opacity 200ms, transform 0.2s"
        )
        self.assertEqual(pairs["transform"], "translate(20px, 5px)")
        self.assertEqual(
            browser.parse_transform(pairs["transform"]), (20.0, 5.0)
        )

    def test_style_setter_starts_opacity_and_transform_transitions(self):
        tab = self.make_tab()
        box = first_element(tab.nodes, "div")
        ctx = JSContext(tab)

        ctx.run(
            "animate.js",
            """
            var box = document.querySelectorAll("div")[0];
            box.style = "opacity:0.9;transform:translate(30px, 0px);"
                + "transition:opacity 0.2s, transform 0.2s";
            """,
        )

        self.assertEqual(
            box.attributes["style"],
            "opacity:0.9;transform:translate(30px, 0px);"
            "transition:opacity 0.2s, transform 0.2s",
        )
        self.assertTrue(tab.needs_style)

        tab.render()
        self.assertIn("opacity", box.animations)
        self.assertIn("transform", box.animations)
        self.assertGreater(float(box.style["opacity"]), 0.1)
        self.assertLess(float(box.style["opacity"]), 0.9)
        self.assertNotEqual(
            browser.parse_transform(box.style["transform"]), (30.0, 0.0)
        )

        for _ in range(10):
            tab.run_animation_frame()

        self.assertEqual(float(box.style["opacity"]), 0.9)
        self.assertEqual(
            browser.parse_transform(box.style["transform"]), (30.0, 0.0)
        )
        self.assertEqual(box.animations, {})

    def test_transform_is_present_in_paint_list_and_hit_testing(self):
        tab = self.make_tab()
        box = first_element(tab.nodes, "div")
        box.attributes["style"] = (
            "transform:translate(100px, 0px);"
            "transition:transform 0.2s"
        )
        tab.set_needs_render()
        tab.render()

        self.assertTrue(
            any(isinstance(cmd, Transform) for cmd in tab.display_list)
        )
        self.assertIs(tab.hit_test(120, 25), box)


if __name__ == "__main__":
    unittest.main()
