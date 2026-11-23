# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""Unit tests for offline-only Kiwix search and QueryMixin integration."""

import json
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from ai_responder import AIResponder
from core import kiwix


SAMPLE_SEARCH_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Search: hypothermia</title>
    <item>
      <title>Hypothermia</title>
      <link>/content/wikipedia_en_medicine_maxi_2026-10/Hypothermia</link>
      <description>...<b>hypothermia</b> occurs when core body temp drops below 35 C...</description>
      <book><title>WikiMed Medical Encyclopedia</title></book>
    </item>
    <item>
      <title>Hypothermia</title>
      <link>/content/wikipedia_en_all_nopic_2026-06/Hypothermia</link>
      <description>...duplicate title in full Wikipedia...</description>
      <book><title>Wikipedia</title></book>
    </item>
    <item>
      <title>Cold Shock Response Quiz</title>
      <link>/content/appropedia_en_all_maxi_2026-02/Cold/Quiz</link>
      <description>...quiz item with weak relevance...</description>
      <book><title>Appropedia</title></book>
    </item>
  </channel>
</rss>
"""

SAMPLE_ARTICLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Hypothermia</title>
  <style>.infobox { display: none; }</style>
  <script>console.log("ignore me");</script>
</head>
<body>
  <nav>Skip navigation menu links and site header items here.</nav>
  <p>Not to be confused with Hyperthermia.</p>
  <p>Hypothermia is defined as a body core temperature below 35.0 C (95.0 F) in humans and causes shivering and confusion.</p>
  <table><tr><td>Infobox cell text that should be skipped from prose extraction.</td></tr></table>
  <p>Unrelated historical paragraph mentioning polar exploration statistics from the nineteenth century in detail.</p>
  <p>To treat mild hypothermia, move the person to a warm dry place, remove wet clothing, wrap in blankets, and offer warm sweet liquids.<sup>[1]</sup></p>
</body>
</html>
"""


class TestKiwixClient(unittest.TestCase):
    def setUp(self):
        kiwix.reset_online_cache()

    def tearDown(self):
        kiwix.reset_online_cache()

    def test_should_search_kiwix_filtering(self):
        skipped = [
            "", "   ", "ok", "!ai -s", "/help",
            "hi", "Hello!", "thanks.", "Roger", "ping", "good morning",
            "What is my SNR and RSSI right now?",
            "How many hops away was that repeater?",
            "Who is online on MeshCore?",
            "Remind me in 10 minutes to check the node",
            "Cancel all my alerts",
            "Check my battery and my location please",
        ]
        for q in skipped:
            with self.subTest(skipped=q):
                self.assertFalse(kiwix.should_search_kiwix(q))

        allowed = [
            "how do I treat hypothermia?",
            "how to replace iphone battery",
            "12v battery solar charge controller wiring",
            "how to purify water with bleach",
            "symptoms of severe dehydration",
        ]
        for q in allowed:
            with self.subTest(allowed=q):
                self.assertTrue(kiwix.should_search_kiwix(q))

    def test_extract_search_terms(self):
        pattern, rel = kiwix.extract_search_terms("How do I treat hypothermia safely?")
        self.assertEqual(pattern, "hypothermia")
        self.assertIn("treat", rel)
        self.assertIn("hypothermia", rel)

        pattern2, _ = kiwix.extract_search_terms("how to wire a solar panel charge controller")
        self.assertEqual(pattern2, "wire solar panel charge controller")

    def test_is_online_cache_and_failure(self):
        mock_sock = MagicMock()
        with patch("core.kiwix.socket.create_connection", return_value=mock_sock) as mock_conn:
            self.assertTrue(kiwix.is_online(cache_ttl=60.0))
            self.assertTrue(kiwix.is_online(cache_ttl=60.0))
            self.assertEqual(mock_conn.call_count, 1)
            mock_sock.close.assert_called_once()

        kiwix.reset_online_cache()
        with patch("core.kiwix.socket.create_connection", side_effect=OSError("network down")) as mock_conn:
            self.assertFalse(kiwix.is_online(cache_ttl=60.0))
            self.assertEqual(mock_conn.call_count, len(kiwix.DEFAULT_PROBE_HOSTS))

    def test_search_kiwix_xml_dedupe_suppression_and_html_extraction(self):
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.content = SAMPLE_SEARCH_XML

        article_resp = MagicMock()
        article_resp.status_code = 200
        article_resp.encoding = "utf-8"
        article_resp.text = SAMPLE_ARTICLE_HTML

        with patch("core.kiwix.requests.get", side_effect=[search_resp, article_resp]) as mock_get:
            res = kiwix.search_kiwix(
                "how to treat hypothermia",
                base_url="http://kiwix:8080",
                max_results=2,
                max_chars=900,
                timeout=5.0,
            )

        self.assertIsNotNone(res)
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", res)
        self.assertIn("Hypothermia (WikiMed Medical Encyclopedia):", res)
        self.assertIn("35.0 C (95.0 F)", res)
        self.assertIn("remove wet clothing", res)
        # Ensure boilerplate, hatnotes, scripts, tables, and dominated quiz/duplicate hits are excluded
        self.assertNotIn("Not to be confused", res)
        self.assertNotIn("ignore me", res)
        self.assertNotIn("Infobox cell text", res)
        self.assertNotIn("Cold Shock Response Quiz", res)
        self.assertNotIn("duplicate title", res)
        # Only 1 search + 1 article fetch (dominated #2 was suppressed cleanly)
        self.assertEqual(mock_get.call_count, 2)

    def test_search_kiwix_secondary_two_term_fallback_and_snippet_fallback(self):
        empty_title_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>Miscellaneous Notes</title>
            <link>/content/wiki/Misc</link>
            <description>Unrelated <b>topic</b> text.</description>
            <book><title>Wikipedia</title></book>
          </item>
        </channel></rss>"""

        better_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>Portable water purification</title>
            <link>/content/wikimed/Portable_water_purification</link>
            <description>Use <b>bleach</b> (sodium hypochlorite) drops to disinfect clear <b>water</b>.</description>
            <book><title>WikiMed</title></book>
          </item>
        </channel></rss>"""

        r1 = MagicMock(status_code=200, content=empty_title_xml)
        r2 = MagicMock(status_code=200, content=better_xml)
        with patch("core.kiwix.requests.get", side_effect=[r1, r2, requests.Timeout("article timeout")]) as mock_get:
            res = kiwix.search_kiwix(
                "purify drinking water bleach emergency",
                base_url="http://kiwix:8080",
                max_results=2,
                max_chars=900,
            )

        self.assertIsNotNone(res)
        self.assertIn("Portable water purification (WikiMed):", res)
        # Full snippet including <b> tag inner text preserved by itertext() fallback
        self.assertIn("Use bleach (sodium hypochlorite) drops to disinfect clear water.", res)
        self.assertEqual(mock_get.call_count, 3)

    def test_search_kiwix_graceful_failure_when_unreachable(self):
        with patch("core.kiwix.requests.get", side_effect=requests.ConnectionError("kiwix down")):
            res = kiwix.search_kiwix("how to treat hypothermia", base_url="http://kiwix:8080")
        self.assertIsNone(res)


class TestQueryMixinKiwixIntegration(unittest.TestCase):
    def setUp(self):
        kiwix.reset_online_cache()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.history_dir = os.path.join(self.tmp_dir.name, "history")
        self.conv_dir = os.path.join(self.tmp_dir.name, "conversations")
        self.config_file = os.path.join(self.tmp_dir.name, "config.json")
        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.conv_dir, exist_ok=True)
        with open(self.config_file, "w") as f:
            json.dump({
                "allowed_channels": [0],
                "admin_nodes": ["!admin"],
                "current_provider": "gemini",
            }, f)

        self.p_cfg = patch("config.CONFIG_FILE", self.config_file)
        self.p_hist = patch("config.HISTORY_DIR", self.history_dir)
        self.p_conv = patch("config.CONVERSATIONS_DIR", self.conv_dir)
        self.p_cfg.start()
        self.p_hist.start()
        self.p_conv.start()

        self.responder = AIResponder(history_dir=self.history_dir)
        self.responder.config.set("kiwix_url", "http://kiwix:8080")
        self.responder.config.set("kiwix_offline_only", True)
        self.responder.meshtastic = MagicMock()
        self.responder.meshtastic.get_node_info.return_value = {"user": {"id": "!bot"}}
        self.responder.meshtastic.get_node_metadata.return_value = "Node !u1 Batt:90%"
        self.responder.meshtastic._get_node_by_id.return_value = None
        self.responder.meshtastic.is_connected.return_value = True
        self.responder.send_response = MagicMock()
        logging.getLogger("AI-Responder").setLevel(logging.CRITICAL)

    def tearDown(self):
        if hasattr(self, "responder") and hasattr(self.responder, "_bg_executor"):
            self.responder._bg_executor.shutdown(wait=True)
        self.p_cfg.stop()
        self.p_hist.stop()
        self.p_conv.stop()
        self.tmp_dir.cleanup()
        kiwix.reset_online_cache()

    def test_cloud_success_never_calls_kiwix(self):
        self.responder.config.set("current_provider", "gemini")
        mock_gemini = MagicMock()
        mock_gemini.name = "Gemini"
        mock_gemini.supports_tools = True
        mock_gemini.get_response.return_value = "Warm the person slowly."

        with patch("ai_responder.get_provider", return_value=mock_gemini), \
             patch("core.query_mixin.kiwix.search_kiwix") as mock_kiwix:
            self.responder._process_ai_query_thread("How to treat hypothermia?", "!u1", "!bot", 0)

        mock_kiwix.assert_not_called()
        sent = self.responder.send_response.call_args[0][0]
        self.assertEqual(sent, "Warm the person slowly.")

    def test_cloud_outage_failover_injects_kiwix_and_keeps_history_clean(self):
        self.responder.config.set("current_provider", "gemini")
        mock_gemini = MagicMock()
        mock_gemini.name = "Gemini"
        mock_gemini.supports_tools = True
        mock_gemini.get_response.return_value = "🌐 Connection failed. Check internet."

        mock_ollama = MagicMock()
        mock_ollama.name = "Ollama"
        mock_ollama.supports_tools = False
        mock_ollama.get_response.return_value = "Move indoors and rewarm gradually."

        kiwix_block = "[OFFLINE REFERENCE (Kiwix)]\nHypothermia: Remove wet clothing.\n[/OFFLINE REFERENCE]"

        with patch("ai_responder.get_provider", side_effect=lambda name, cfg: mock_ollama if name == "ollama" else mock_gemini), \
             patch("core.query_mixin.kiwix.is_online") as mock_online, \
             patch("core.query_mixin.kiwix.search_kiwix", return_value=kiwix_block) as mock_kiwix:
            self.responder._process_ai_query_thread("How to treat hypothermia?", "!u1", "!bot", 0, is_dm=True)

        mock_online.assert_not_called()  # Cloud outage already proved offline
        mock_kiwix.assert_called_once()
        prompt_arg, hist_arg = mock_ollama.get_response.call_args[0][:2]
        self.assertIn("[RADIO CONTEXT]", prompt_arg)
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", prompt_arg)
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", hist_arg[-1]["content"])

        # Stored history stays clean without injected blocks
        stored = self.responder.history["DM:!u1"]
        self.assertIn("How to treat hypothermia?", stored[0]["content"])
        self.assertNotIn("[OFFLINE REFERENCE (Kiwix)]", stored[0]["content"])
        self.assertNotIn("[RADIO CONTEXT]", stored[0]["content"])
        sent = self.responder.send_response.call_args[0][0]
        self.assertEqual(sent, "[Offline] Move indoors and rewarm gradually.")

    def test_direct_ollama_offline_only_skips_kiwix_when_online(self):
        self.responder.config.set("current_provider", "ollama")
        self.responder.config.set("kiwix_offline_only", True)
        mock_ollama = MagicMock()
        mock_ollama.name = "Ollama"
        mock_ollama.supports_tools = False
        mock_ollama.get_response.return_value = "Direct online-mode Ollama answer."

        with patch("ai_responder.get_provider", return_value=mock_ollama), \
             patch("core.query_mixin.kiwix.is_online", return_value=True), \
             patch("core.query_mixin.kiwix.search_kiwix") as mock_kiwix:
            self.responder._process_ai_query_thread("How to treat hypothermia?", "!u1", "!bot", 0)

        mock_kiwix.assert_not_called()
        prompt_arg = mock_ollama.get_response.call_args[0][0]
        self.assertNotIn("[OFFLINE REFERENCE (Kiwix)]", prompt_arg)

    def test_direct_ollama_offline_only_injects_kiwix_when_offline(self):
        self.responder.config.set("current_provider", "ollama")
        self.responder.config.set("kiwix_offline_only", True)
        mock_ollama = MagicMock()
        mock_ollama.name = "Ollama"
        mock_ollama.supports_tools = False
        mock_ollama.get_response.return_value = "Grounded offline answer."

        kiwix_block = "[OFFLINE REFERENCE (Kiwix)]\nHypothermia: Core temp < 35C.\n[/OFFLINE REFERENCE]"

        with patch("ai_responder.get_provider", return_value=mock_ollama), \
             patch("core.query_mixin.kiwix.is_online", return_value=False), \
             patch("core.query_mixin.kiwix.search_kiwix", return_value=kiwix_block) as mock_kiwix:
            self.responder._process_ai_query_thread("How to treat hypothermia?", "!u1", "!bot", 0, is_dm=True)

        mock_kiwix.assert_called_once()
        prompt_arg, hist_arg = mock_ollama.get_response.call_args[0][:2]
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", prompt_arg)
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", hist_arg[-1]["content"])
        stored = self.responder.history["DM:!u1"]
        self.assertIn("How to treat hypothermia?", stored[0]["content"])
        self.assertNotIn("[OFFLINE REFERENCE (Kiwix)]", stored[0]["content"])

    def test_direct_ollama_offline_only_false_calls_kiwix_even_when_online(self):
        self.responder.config.set("current_provider", "ollama")
        self.responder.config.set("kiwix_offline_only", False)
        mock_ollama = MagicMock()
        mock_ollama.name = "Ollama"
        mock_ollama.supports_tools = False
        mock_ollama.get_response.return_value = "Forced Kiwix answer."

        kiwix_block = "[OFFLINE REFERENCE (Kiwix)]\nHypothermia: Core temp < 35C.\n[/OFFLINE REFERENCE]"

        with patch("ai_responder.get_provider", return_value=mock_ollama), \
             patch("core.query_mixin.kiwix.is_online", return_value=True) as mock_online, \
             patch("core.query_mixin.kiwix.search_kiwix", return_value=kiwix_block) as mock_kiwix:
            resp = self.responder.get_ai_response("How to treat hypothermia?", "DM:!u1")

        mock_online.assert_not_called()
        mock_kiwix.assert_called_once()
        self.assertEqual(resp, "Forced Kiwix answer.")
        self.assertIn("[OFFLINE REFERENCE (Kiwix)]", mock_ollama.get_response.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
