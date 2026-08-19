from __future__ import annotations

import unittest

from media_dup_finder.normalization import normalize_filename


class NormalizationTests(unittest.TestCase):
    def test_catalog_variants_share_key(self) -> None:
        names = ["MIDA-630.MP4", "MIDA-630-C.MP4", "MIDA-630-4k.MOV"]
        keys = {normalize_filename(name).catalog_key for name in names}
        self.assertEqual(keys, {"mida-630"})

    def test_fc2_catalog(self) -> None:
        self.assertEqual(
            normalize_filename("FC2-PPV-1234567_1080p.mp4").catalog_key,
            "fc2-ppv-1234567",
        )

    def test_chinese_promotional_prefix_and_first_alias(self) -> None:
        base = normalize_filename("寒战.mp4")
        numbered = normalize_filename("经典剧情《寒战1》.mkv")
        self.assertIn("寒战", base.aliases)
        self.assertIn("寒战", numbered.aliases)
        self.assertEqual(numbered.primary, "寒战1")

    def test_second_sequel_does_not_drop_number(self) -> None:
        self.assertEqual(normalize_filename("寒战2.mp4").aliases, ("寒战2",))

    def test_movie_year_is_not_catalog_number(self) -> None:
        info = normalize_filename("Movie.Title.2020.1080p.mkv")
        self.assertIsNone(info.catalog_key)
        self.assertIn("2020", info.primary)

    def test_short_movie_title_year_is_not_catalog(self) -> None:
        info = normalize_filename("IT.2017.1080p.mkv")
        self.assertIsNone(info.catalog_key)
        self.assertIn("it", info.aliases)

    def test_explicit_part_marker_is_preserved(self) -> None:
        self.assertEqual(normalize_filename("MIDA-630-CD2.mp4").part_marker, "part2")

    def test_website_prefix_and_release_group_are_removed(self) -> None:
        info = normalize_filename("[www.example.com]寒战.1080p-RARBG.mp4")
        self.assertEqual(info.primary, "寒战")

    def test_site_brand_left_after_domain_is_removed(self) -> None:
        info = normalize_filename("[电影天堂www.example.com]流浪地球.2019.2160p.mkv")
        self.assertEqual(info.primary, "流浪地球2019")

    def test_compound_chinese_language_tag_is_removed(self) -> None:
        self.assertEqual(normalize_filename("流浪地球【国语中字】4K.mkv").primary, "流浪地球")

    def test_traditional_chinese_uses_matching_form(self) -> None:
        self.assertEqual(normalize_filename("無間道.mkv").primary, "无间道")

    def test_roman_sequel_suffix_is_normalized(self) -> None:
        self.assertEqual(normalize_filename("寒战Ⅱ.mp4").primary, "寒战2")

    def test_roman_sequel_before_release_suffix_is_normalized(self) -> None:
        info = normalize_filename("無間道Ⅱ.2003.1080p.BluRay.mkv")
        self.assertEqual(info.primary, "无间道22003")
        self.assertIn("无间道2", info.aliases)

    def test_roman_word_in_ordinary_title_is_preserved(self) -> None:
        self.assertEqual(normalize_filename("I.Robot.2004.mkv").primary, "irobot2004")

    def test_catalog_leading_zero_is_normalized(self) -> None:
        self.assertEqual(normalize_filename("ABC-001.mp4").catalog_key, "abc-1")
        self.assertEqual(normalize_filename("ABC-1.mkv").catalog_key, "abc-1")

    def test_additional_catalog_families(self) -> None:
        examples = {
            "1pondo-123456_001.mp4": "1pondo-123456-1",
            "10musume_123456-02.mp4": "10musume-123456-2",
            "Tokyo-Hot-n01234.mp4": "tokyo-hot-n1234",
            "S2MBD-001.mp4": "s2mbd-1",
        }
        for filename, expected in examples.items():
            with self.subTest(filename=filename):
                self.assertEqual(normalize_filename(filename).catalog_key, expected)

    def test_dated_series_episode_is_a_work_identity_not_a_catalog(self) -> None:
        info = normalize_filename("brothalovers.15.07.28.lynna.nilsson.mp4")
        self.assertIsNone(info.catalog_key)
        self.assertEqual(info.identity_kind, "dated_episode")
        self.assertEqual(info.series_key, "brothalovers")
        self.assertEqual(info.episode_date, "2015-07-28")
        self.assertIn("lynna", info.work_key)

    def test_numeric_suffix_after_catalog_is_a_part_marker(self) -> None:
        self.assertEqual(normalize_filename("kavr-253-2.mp4").catalog_key, "kavr-253")
        self.assertEqual(normalize_filename("kavr-253-2.mp4").part_marker, "part2")
        self.assertEqual(normalize_filename("kavr-253-8.mp4").part_marker, "part8")

    def test_letter_suffix_after_catalog_can_be_a_segment(self) -> None:
        self.assertEqual(normalize_filename("kavr-253-A.mp4").part_marker, "segment:A")
        self.assertEqual(normalize_filename("kavr-253-B.mp4").part_marker, "segment:B")
        self.assertIsNone(normalize_filename("MIDA-630-C.mp4").part_marker)

    def test_season_episode_is_a_structured_work_identity(self) -> None:
        info = normalize_filename("The.Show.S02E07.1080p.mkv")
        self.assertEqual(info.identity_kind, "series_episode")
        self.assertEqual(info.series_key, "theshow")
        self.assertEqual(info.episode_id, "s02e07")

    def test_episode_only_and_chinese_episode_are_structured_identities(self) -> None:
        english = normalize_filename("The.Show.E07.1080p.mkv")
        chinese = normalize_filename("纪录片.第12集.4K.mp4")
        self.assertEqual(english.identity_kind, "series_episode")
        self.assertEqual(english.episode_id, "e007")
        self.assertEqual(chinese.identity_kind, "series_episode")
        self.assertEqual(chinese.episode_id, "e012")

    def test_compact_eight_digit_date_is_a_dated_episode(self) -> None:
        info = normalize_filename("brothalovers.20150728.lynna.nilsson.mp4")
        self.assertEqual(info.identity_kind, "dated_episode")
        self.assertEqual(info.series_key, "brothalovers")
        self.assertEqual(info.episode_date, "2015-07-28")

    def test_site_brand_is_removed_but_unique_at_identifier_is_preserved(self) -> None:
        info = normalize_filename("2048社区 - fun2048.com@fc1298546.mp4")
        self.assertEqual(info.catalog_key, "fc-1298546")
        self.assertNotIn("2048社区", info.primary)

    def test_chinese_character_inside_title_is_not_a_part_marker(self) -> None:
        self.assertIsNone(normalize_filename("上海滩.mp4").part_marker)
        self.assertEqual(normalize_filename("电影-上.mp4").part_marker, "segment:上")

    def test_year_alias_does_not_create_false_first_part_alias(self) -> None:
        info = normalize_filename("The Thing 2011.mkv")
        self.assertEqual(info.years, (2011,))
        self.assertIn("thething", info.aliases)
        self.assertNotIn("thething201", info.aliases)

    def test_title_number_is_not_mistaken_for_release_year(self) -> None:
        info = normalize_filename("2001.A.Space.Odyssey.1968.1080p.mkv")
        self.assertEqual(info.years, (1968,))
        self.assertIn("2001aspaceodyssey", info.aliases)
        title_only = normalize_filename("2001.A.Space.Odyssey.mkv")
        self.assertEqual(title_only.years, ())

    def test_year_only_movie_title_is_preserved(self) -> None:
        info = normalize_filename("1917.1080p.mkv")
        self.assertEqual(info.years, ())
        self.assertEqual(info.primary, "1917")


if __name__ == "__main__":
    unittest.main()
