#!/usr/bin/env python3
"""
NBA Highlights Reel Creator

Builds a player highlight reel by:
1) Using nba_api to identify player + recent games.
2) Pulling highlight-like events from play-by-play.
3) Asking nba_api video endpoints for event media.
4) Falling back to NBA.com search + page extraction.
5) Downloading clips and stitching with ffmpeg.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
import yt_dlp
from bs4 import BeautifulSoup

try:
    from nba_api.stats.endpoints import playbyplayv2, playergamelog, videodetailsasset
    from nba_api.live.nba.endpoints import boxscore as live_boxscore
    from nba_api.live.nba.endpoints import playbyplay as live_playbyplay
    from nba_api.stats.static import players, teams

    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    print("Warning: nba_api not installed. Install with: pip install nba_api")


@dataclass
class ScriptSettings:
    """One-click run settings used when no CLI args are passed."""

    player_name: str = "Derrick White"
    season: str = "2025-26"
    event_type: str = "blocks"  # highlights | blocks
    max_highlights: int = 999
    all_games: bool = False
    max_games: int = 6
    # Game selection filters (applied in order: date/opponent, then recent index fallback)
    target_date: Optional[str] = "2025-12-30"  # YYYY-MM-DD or "Dec 30, 2025"
    target_opponent: Optional[str] = "UTA"  # Team abbreviation in matchup string
    recent_game_index: int = 0  # 0=most recent, 1=second-most recent, etc.
    # Output controls
    output_basename: str = "auto_reel.mp4"
    trim_last_seconds: int = 10
    auto_stitch: bool = True
    downloads_dir: str = "downloads"
    output_dir: str = "output"


# Edit this block once, then run: python nba_highlights.py
SCRIPT_SETTINGS = ScriptSettings()


def _current_season_str() -> str:
    """Return season string like 2025-26."""
    from datetime import datetime

    now = datetime.now()
    start_year = now.year if now.month >= 9 else now.year - 1
    end_short = str((start_year + 1) % 100).zfill(2)
    return f"{start_year}-{end_short}"


class NBAHighlightsFinder:
    """Finds player highlights using nba_api first, then site fallback."""

    BASE_URL = "https://www.nba.com"
    SEARCH_URL = f"{BASE_URL}/search?q={{query}}"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    HIGHLIGHT_TERMS = (
        "dunk",
        "3pt",
        "three",
        "step back",
        "fadeaway",
        "alley",
        "block",
        "steal",
        "and-1",
        "buzzer",
    )
    BLOCK_TERMS = (" block ", "blocks", "blk")

    def __init__(self, player_name: str, season: Optional[str] = None) -> None:
        self.player_name = player_name.strip()
        self.season = season or _current_season_str()
        self.player_id: Optional[int] = None
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.highlights: List[Dict] = []
        self._last_name = self.player_name.split()[-1].lower() if self.player_name else ""
        self._team_id_by_abbrev: Dict[str, int] = {}
        if NBA_API_AVAILABLE:
            self._team_id_by_abbrev = {
                str(team.get("abbreviation", "")).upper(): int(team.get("id", 0) or 0)
                for team in teams.get_teams()
            }
            self._find_player_id()

    def _find_player_id(self) -> Optional[int]:
        """Find closest player match by full name."""
        try:
            match = players.find_players_by_full_name(self.player_name)
            if not match:
                print(f"Warning: Could not find player ID for '{self.player_name}'")
                return None
            # Prefer active players if multiple
            match.sort(key=lambda p: (not p.get("is_active", False), p["full_name"]))
            best = match[0]
            self.player_id = int(best["id"])
            print(f"Found player: {best['full_name']} (ID: {self.player_id})")
            return self.player_id
        except Exception as exc:  # noqa: BLE001
            print(f"Error finding player ID: {exc}")
            return None

    def get_recent_games(self, max_games: Optional[int] = 6) -> List[Dict]:
        """Return games with valid GAME_ID values."""
        if not NBA_API_AVAILABLE or not self.player_id:
            return []
        try:
            log = playergamelog.PlayerGameLog(
                player_id=self.player_id,
                season=self.season,
                season_type_all_star="Regular Season",
                timeout=20,
            )
            games_df = log.get_data_frames()[0]
            if games_df.empty:
                return []

            recent = []
            rows = games_df if max_games is None else games_df.head(max_games)
            for _, row in rows.iterrows():
                game_id = str(row.get("Game_ID", "")).strip()
                if not game_id:
                    continue
                matchup = str(row.get("MATCHUP", ""))
                player_team_abbrev = matchup.split(" ")[0].upper() if matchup else ""
                recent.append(
                    {
                        "game_id": game_id,
                        "team_id": int(self._team_id_by_abbrev.get(player_team_abbrev, 0)),
                        "date": str(row.get("GAME_DATE", "")),
                        "matchup": matchup,
                        "pts": int(row.get("PTS", 0)),
                        "reb": int(row.get("REB", 0)),
                        "ast": int(row.get("AST", 0)),
                    }
                )
            return recent
        except Exception as exc:  # noqa: BLE001
            print(f"Error getting recent games: {exc}")
            return []

    def _is_highlight_event(self, row: Dict) -> bool:
        """Heuristic to keep likely highlight-worthy events."""
        desc = " ".join(
            str(row.get(key, ""))
            for key in ("HOMEDESCRIPTION", "VISITORDESCRIPTION", "NEUTRALDESCRIPTION")
        ).lower()
        event_type = int(row.get("EVENTMSGTYPE", 0) or 0)
        if self._last_name and self._last_name not in desc:
            return False
        if any(term in desc for term in self.HIGHLIGHT_TERMS):
            return True
        # Made shot / turnover+steal / block-ish can still be useful.
        return event_type in (1, 5)

    def _is_block_event(self, action: Dict) -> bool:
        """Return True when the action looks like a block event."""
        action_type = str(action.get("actionType", "")).lower()
        sub_type = str(action.get("subType", "")).lower()
        if "block" in action_type or "block" in sub_type:
            return True
        # Avoid matching "MISS ... blocked" shot descriptions as standalone block clips.
        return False

    def _event_matches_requested_type(self, action: Dict, event_type: str) -> bool:
        """Filter events by requested type."""
        if event_type == "blocks":
            return self._is_block_event(action)

        # Default behavior: general highlight heuristic
        row_dict = {
            "HOMEDESCRIPTION": action.get("description", ""),
            "VISITORDESCRIPTION": action.get("description", ""),
            "NEUTRALDESCRIPTION": action.get("description", ""),
            "EVENTMSGTYPE": 1 if action.get("isFieldGoal") else 0,
        }
        return self._is_highlight_event(row_dict)

    def _extract_urls_from_videodetailsasset(
        self, team_id: int, game_id: str, event_num: int
    ) -> List[str]:
        """
        Extract direct media URLs from VideoDetailsAsset.
        This endpoint currently returns playable videos.nba.com MP4 URLs.
        """
        urls: List[str] = []
        try:
            if not self.player_id or not team_id:
                return []
            payload = videodetailsasset.VideoDetailsAsset(
                team_id=team_id,
                player_id=self.player_id,
                game_id_nullable=game_id,
                season=self.season,
                season_type_all_star="Regular Season",
                timeout=25,
            )
            result_sets = payload.get_dict().get("resultSets", {})
            meta_urls = result_sets.get("Meta", {}).get("videoUrls", [])
            playlist = result_sets.get("playlist", [])
            for index, event in enumerate(playlist):
                try:
                    if int(event.get("ei", -1)) != int(event_num):
                        continue
                except (TypeError, ValueError):
                    continue
                if index >= len(meta_urls):
                    continue
                video = meta_urls[index]
                for key in ("lurl", "murl", "surl"):
                    media_url = str(video.get(key, "")).strip()
                    if media_url.startswith(("http://", "https://")):
                        urls.append(media_url)
        except Exception:
            return []
        return urls

    def _get_event_video_candidates(
        self, game_id: str, team_id: int, event_num: int, event_title: str
    ) -> List[Dict]:
        """Get candidate URLs for a game event."""
        candidates: List[Dict] = []

        # 1) Direct MP4 URLs from VideoDetailsAsset
        direct_urls = self._extract_urls_from_videodetailsasset(team_id, game_id, event_num)
        for url in direct_urls:
            candidates.append({"url": url, "title": event_title, "description": f"Event {event_num}"})

        # 2) stats event page URL fallback (canonical per-event page)
        stats_event_url = (
            f"https://www.nba.com/stats/events?CFID=&CFPARAMS=&GameEventID={event_num}"
            f"&GameID={game_id}&Season={self.season}&flag=1&title={quote(event_title)}"
        )
        candidates.append(
            {"url": stats_event_url, "title": event_title, "description": f"Event {event_num}"}
        )

        # 3) Search fallback based on exact event text (often resolves to /watch/video)
        query_url = self.SEARCH_URL.format(query=quote(event_title))
        candidates.extend(self._search_nba_videos_from_url(query_url))
        return candidates

    def _build_context_measure_url(self, game_id: str, team_id: int, context_measure: str) -> str:
        """Build NBA stats game context-measure URL (e.g. BLK sequence view)."""
        return (
            "https://www.nba.com/stats/events"
            "?CFID=&CFPARAMS="
            f"&ContextMeasure={quote(context_measure)}"
            "&EndPeriod=0&EndRange=28800"
            f"&GameID={game_id}"
            f"&PlayerID={self.player_id or 0}"
            "&RangeType=0"
            f"&Season={quote(self.season)}"
            "&SeasonType=Regular%20Season"
            "&StartPeriod=0&StartRange=0"
            f"&TeamID={team_id}"
            "&flag=1&sct=plot&section=game"
        )

    def _get_context_measure_video_candidates(
        self, game_id: str, team_id: int, context_measure: str
    ) -> List[Dict]:
        """
        Pull clip candidates directly from VideoDetailsAsset context measure.
        This aligns with URLs like ContextMeasure=BLK shown on nba.com/stats/events.
        """
        candidates: List[Dict] = []
        if not self.player_id or not team_id:
            return candidates

        try:
            payload = videodetailsasset.VideoDetailsAsset(
                team_id=team_id,
                player_id=self.player_id,
                context_measure_detailed=context_measure,
                game_id_nullable=game_id,
                season=self.season,
                season_type_all_star="Regular Season",
                timeout=25,
            )
            result_sets = payload.get_dict().get("resultSets", {})
            meta_urls = result_sets.get("Meta", {}).get("videoUrls", [])
            playlist = result_sets.get("playlist", [])

            for index, event in enumerate(playlist):
                event_num = int(event.get("ei", 0) or 0)
                event_title = str(event.get("dsc", "")).strip()
                if not event_title:
                    event_title = f"{self.player_name} {context_measure}"
                # Normalize "J. Huff ..." -> "Huff ..." for cleaner titles/links.
                event_title = re.sub(r"^[A-Z]\.\s+", "", event_title)
                if context_measure.upper() == "BLK":
                    last = self.player_name.split()[-1] if self.player_name else "Player"
                    event_title = f"{last} BLOCK ({index + 1} BLK)"

                if index < len(meta_urls):
                    video = meta_urls[index]
                    media_url = ""
                    for key in ("lurl", "murl", "surl"):
                        candidate_url = str(video.get(key, "")).strip()
                        if candidate_url.startswith(("http://", "https://")):
                            media_url = candidate_url
                            break
                    if media_url:
                        candidates.append(
                            {
                                "url": media_url,
                                "title": event_title,
                                "description": f"Event {event_num}",
                            }
                        )

                stats_event_url = (
                    f"https://www.nba.com/stats/events?CFID=&CFPARAMS=&GameEventID={event_num}"
                    f"&GameID={game_id}&Season={self.season}&flag=1&title={quote(event_title)}"
                )
                candidates.append(
                    {"url": stats_event_url, "title": event_title, "description": f"Event {event_num}"}
                )

            # Also include the context page itself as a human-debuggable fallback source.
            candidates.append(
                {
                    "url": self._build_context_measure_url(game_id, team_id, context_measure),
                    "title": f"{self.player_name} {context_measure} sequence",
                    "description": f"{context_measure} context page",
                }
            )
        except Exception:
            return candidates

        return candidates

    def _search_nba_videos_from_url(self, url: str) -> List[Dict]:
        """Extract candidate video pages from NBA.com search HTML."""
        links: List[Dict] = []
        try:
            response = self.session.get(url, timeout=12)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = tag.get("href", "").strip()
                text = tag.get_text(strip=True)
                if not href:
                    continue
                lower_href = href.lower()
                lower_text = text.lower()
                if any(k in lower_href for k in ("video", "highlight", "play", "watch")):
                    if self._last_name in lower_text or "highlight" in lower_text:
                        links.append(
                            {
                                "url": urljoin(self.BASE_URL, href),
                                "title": text or f"{self.player_name} highlight",
                                "description": "",
                            }
                        )
        except requests.RequestException as exc:
            print(f"Error searching NBA.com: {exc}")
        return links

    def get_video_urls_from_page(self, page_url: str) -> List[str]:
        """Extract media URLs from HTML, including script blobs."""
        try:
            response = self.session.get(page_url, timeout=12)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            urls: List[str] = []

            for video_tag in soup.find_all("video"):
                for source in video_tag.find_all("source"):
                    src = source.get("src", "").strip()
                    if src:
                        urls.append(urljoin(page_url, src))

            for iframe in soup.find_all("iframe"):
                src = iframe.get("src", "").strip()
                if src:
                    urls.append(urljoin(page_url, src))

            # Script fallback for embedded media playlists
            regex = r"https?://[^\"'\\s>]+\\.(?:mp4|m3u8)"
            urls.extend(re.findall(regex, html))

            # de-dup while preserving order
            seen = set()
            deduped = []
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    deduped.append(u)
            return deduped
        except requests.RequestException as exc:
            print(f"Error fetching page {page_url}: {exc}")
            return []

    def get_highlights(
        self, max_results: int = 15, max_games: Optional[int] = 6, event_type: str = "highlights"
    ) -> List[Dict]:
        """Primary highlight discovery entrypoint."""
        print(f"Searching {event_type} for {self.player_name}...")
        found: List[Dict] = []

        # Path A: nba_api structured route (live play-by-play endpoint)
        games = self.get_recent_games(max_games=max_games)
        print(f"Found {len(games)} recent games via nba_api")
        for game in games:
            game_id = game["game_id"]
            team_id = int(game.get("team_id", 0) or 0)
            if event_type == "blocks":
                # Prefer the explicit BLK sequence endpoint; it is closer to NBA.com box-score event view.
                found.extend(self._get_context_measure_video_candidates(game_id, team_id, "BLK"))
                if len(found) >= max_results * 3:
                    break
            try:
                pbp_live = live_playbyplay.PlayByPlay(game_id=game_id, timeout=20)
                actions = pbp_live.get_dict().get("game", {}).get("actions", [])
                if not actions:
                    # Fallback for older endpoint in case live endpoint returns no actions
                    pbp = playbyplayv2.PlayByPlayV2(game_id=game_id, timeout=20)
                    pbp_df = pbp.get_data_frames()[0]
                    actions = pbp_df.to_dict(orient="records") if not pbp_df.empty else []

                for action in actions:
                    person_ids = action.get("personIdsFilter", [])
                    if person_ids:
                        normalized_ids = {int(pid) for pid in person_ids if str(pid).isdigit()}
                        if self.player_id not in normalized_ids:
                            continue
                    else:
                        # Fallback when personIdsFilter is absent
                        p1 = int(action.get("PLAYER1_ID", 0) or 0)
                        p2 = int(action.get("PLAYER2_ID", 0) or 0)
                        p3 = int(action.get("PLAYER3_ID", 0) or 0)
                        if self.player_id not in (p1, p2, p3):
                            continue

                    if not self._event_matches_requested_type(action, event_type):
                        continue

                    event_num = int(action.get("actionNumber") or action.get("EVENTNUM") or 0)
                    if event_num <= 0:
                        continue

                    event_desc = str(action.get("description", "")).strip()
                    if not event_desc:
                        event_desc = f"{self.player_name} - {game['matchup']} ({game['date']})"
                    event_title = event_desc[:140]
                    if self._last_name and self._last_name not in event_title.lower():
                        # Keep query centered on requested player
                        event_title = f"{self.player_name} {event_title}"

                    found.extend(
                        self._get_event_video_candidates(game_id, team_id, event_num, event_title)
                    )
                    if len(found) >= max_results * 3:
                        break
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: could not parse play-by-play for {game_id}: {exc}")

            if len(found) >= max_results * 3:
                break

        # Path B: site search fallback
        if len(found) < max_results:
            fallback_query = quote(f"{self.player_name} {event_type}")
            found.extend(self._search_nba_videos_from_url(self.SEARCH_URL.format(query=fallback_query)))

        # De-dup and cap
        seen_urls = set()
        unique: List[Dict] = []
        for item in found:
            url = item.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append(item)
            if len(unique) >= max_results:
                break

        self.highlights = unique
        print(f"Collected {len(unique)} unique highlight candidates")
        return unique


class VideoDownloader:
    """Download videos with yt-dlp."""

    def __init__(self, output_dir: str = "downloads") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def download_video(self, url: str, filename: str) -> Optional[str]:
        output_path = self.output_dir / filename
        # If caller passes an extension, preserve it. Otherwise let yt-dlp choose.
        outtmpl = (
            str(output_path)
            if output_path.suffix
            else str(output_path.with_name(f"{output_path.name}.%(ext)s"))
        )
        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "quiet": False,
            "no_warnings": False,
            "noplaylist": True,
            "socket_timeout": 45,
            "retries": 3,
            "fragment_retries": 3,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            matches = list(self.output_dir.glob(f"{Path(filename).stem}*"))
            return str(matches[0]) if matches else None
        except Exception as exc:  # noqa: BLE001
            print(f"Error downloading {url}: {exc}")
            return None


def _dedupe_preserve_order(urls: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for url in urls:
        clean = str(url).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _parse_stats_event_url(event_url: str) -> Optional[Dict[str, str]]:
    """Extract GameID/GameEventID/Season from an nba.com stats/events URL."""
    parsed = urlparse(event_url)
    if "nba.com" not in parsed.netloc.lower():
        return None
    if "/stats/events" not in parsed.path:
        return None
    query = parse_qs(parsed.query)
    game_id = (query.get("GameID") or [""])[0]
    event_id = (query.get("GameEventID") or [""])[0]
    season = (query.get("Season") or [_current_season_str()])[0]
    if not game_id or not event_id:
        return None
    return {"game_id": game_id, "event_id": event_id, "season": season}


def _normalize_date_token(value: str) -> str:
    return str(value or "").strip().lower().replace("  ", " ")


def _date_matches(target_date: str, game_date: str) -> bool:
    target = _normalize_date_token(target_date)
    game = _normalize_date_token(game_date)
    if not target or not game:
        return False
    if target == game:
        return True
    # Support YYYY-MM-DD target against "Mon DD, YYYY" source.
    try:
        from datetime import datetime

        if "-" in target:
            target_fmt = datetime.strptime(target, "%Y-%m-%d").strftime("%b %d, %Y").lower()
            return target_fmt == game
    except ValueError:
        return False
    return False


def resolve_direct_media_urls_from_event_url(event_url: str) -> List[str]:
    """
    Resolve direct videos.nba.com clip URLs from an NBA stats event URL.

    Example input:
    https://www.nba.com/stats/events?CFID=&CFPARAMS=&GameEventID=7&GameID=0022500771&Season=2025-26&flag=1&title=...
    """
    if not NBA_API_AVAILABLE:
        return []

    params = _parse_stats_event_url(event_url)
    if not params:
        return []

    game_id = params["game_id"]
    season = params["season"]
    try:
        event_num = int(params["event_id"])
    except ValueError:
        return []

    # Gather involved players for this exact event (usually 1-2 IDs).
    person_ids: List[int] = []
    try:
        pbp_live = live_playbyplay.PlayByPlay(game_id=game_id, timeout=20)
        actions = pbp_live.get_dict().get("game", {}).get("actions", [])
        for action in actions:
            try:
                if int(action.get("actionNumber") or 0) != event_num:
                    continue
            except (TypeError, ValueError):
                continue
            for pid in action.get("personIdsFilter", []):
                if str(pid).isdigit():
                    person_ids.append(int(pid))
            break
    except Exception:
        pass

    # Get both team IDs from the game box score.
    team_ids: List[int] = []
    try:
        box = live_boxscore.BoxScore(game_id=game_id, timeout=20).get_dict().get("game", {})
        home_id = int(box.get("homeTeam", {}).get("teamId") or 0)
        away_id = int(box.get("awayTeam", {}).get("teamId") or 0)
        team_ids = [tid for tid in (home_id, away_id) if tid]
    except Exception:
        pass

    if not person_ids or not team_ids:
        return []

    urls: List[str] = []
    for team_id in team_ids:
        for player_id in _dedupe_preserve_order([str(pid) for pid in person_ids]):
            try:
                payload = videodetailsasset.VideoDetailsAsset(
                    team_id=team_id,
                    player_id=int(player_id),
                    game_id_nullable=game_id,
                    season=season,
                    season_type_all_star="Regular Season",
                    timeout=25,
                )
                result_sets = payload.get_dict().get("resultSets", {})
                meta_urls = result_sets.get("Meta", {}).get("videoUrls", [])
                playlist = result_sets.get("playlist", [])

                for index, event in enumerate(playlist):
                    try:
                        if int(event.get("ei", -1)) != event_num:
                            continue
                    except (TypeError, ValueError):
                        continue
                    if index >= len(meta_urls):
                        continue
                    video = meta_urls[index]
                    for key in ("lurl", "murl", "surl"):
                        candidate = str(video.get(key, "")).strip()
                        if candidate.startswith(("http://", "https://")):
                            urls.append(candidate)
            except Exception:
                continue

    return _dedupe_preserve_order(urls)


def download_video_from_source_url(
    source_url: str,
    output_filename: str = "single_event_clip.mp4",
    output_dir: str = "downloads",
) -> Optional[str]:
    """
    Download a single clip from a source URL.

    For NBA stats event URLs, this first resolves direct videos.nba.com media links.
    """
    downloader = VideoDownloader(output_dir=output_dir)
    candidates: List[str] = []

    # Best path for stats event links: resolve direct media URLs.
    candidates.extend(resolve_direct_media_urls_from_event_url(source_url))

    # Fallbacks: page extraction + source URL itself.
    if "nba.com" in source_url:
        try:
            response = requests.get(source_url, timeout=20)
            response.raise_for_status()
            html = response.text
            candidates.extend(re.findall(r"https?://[^\"'\\s>]+\\.(?:mp4|m3u8)", html))
        except requests.RequestException:
            pass
    candidates.append(source_url)

    for candidate_url in _dedupe_preserve_order(candidates):
        path = downloader.download_video(candidate_url, output_filename)
        if path:
            return path
    return None


def trim_last_seconds_of_clips(
    input_paths: List[str],
    seconds: int,
    output_dir: str,
) -> List[str]:
    """Trim each clip to last N seconds (or full length if shorter)."""
    if seconds <= 0:
        return input_paths

    stitcher = VideoStitcher(output_dir=output_dir)
    ffmpeg_bin = stitcher.ffmpeg_bin
    if not ffmpeg_bin:
        print("Warning: ffmpeg not available, skipping trim step.")
        return input_paths

    out_dir = Path(output_dir) / "trimmed"
    out_dir.mkdir(parents=True, exist_ok=True)
    trimmed: List[str] = []
    for src in input_paths:
        src_path = Path(src)
        out_path = out_dir / src_path.name
        cmd = [
            ffmpeg_bin,
            "-y",
            "-sseof",
            f"-{seconds}",
            "-i",
            str(src_path),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            trimmed.append(str(out_path))
        else:
            print(f"Warning: trim failed for {src_path.name}, keeping original clip")
            trimmed.append(str(src_path))
    return trimmed


class VideoStitcher:
    """Concatenate clips into a final reel."""

    def __init__(self, output_dir: str = "output") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.ffmpeg_bin = self._resolve_ffmpeg_bin()

    def _resolve_ffmpeg_bin(self) -> Optional[str]:
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    def _check_ffmpeg(self) -> bool:
        if not self.ffmpeg_bin:
            return False
        try:
            subprocess.run([self.ffmpeg_bin, "-version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def stitch_videos(self, video_paths: List[str], output_filename: str) -> Optional[str]:
        if not self._check_ffmpeg():
            print("Error: ffmpeg is not available.")
            print("Install system ffmpeg or run: pip install imageio-ffmpeg")
            return None

        valid = [v for v in video_paths if v and os.path.exists(v)]
        if not valid:
            print("Error: No valid downloaded video files found")
            return None

        output_path = self.output_dir / output_filename
        concat_file = self.output_dir / "concat_list.txt"
        try:
            with concat_file.open("w", encoding="utf-8") as handle:
                for path in valid:
                    handle.write(f"file '{os.path.abspath(path)}'\n")

            # Try stream copy first (fast), then re-encode fallback.
            cmd_copy = [
                self.ffmpeg_bin,
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                "-y",
                str(output_path),
            ]
            result = subprocess.run(cmd_copy, capture_output=True, text=True)
            if result.returncode == 0:
                return str(output_path)

            cmd_reencode = [
                self.ffmpeg_bin,
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-y",
                str(output_path),
            ]
            result = subprocess.run(cmd_reencode, capture_output=True, text=True)
            if result.returncode == 0:
                return str(output_path)

            print("Error stitching videos:")
            print(result.stderr)
            return None
        finally:
            if concat_file.exists():
                concat_file.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a player highlight reel")
    parser.add_argument("player_name", nargs="?", default=None, help='Player name, e.g. "LeBron James"')
    parser.add_argument("max_highlights", nargs="?", default=10, type=int)
    parser.add_argument("--season", default=None, help='Season like "2025-26"')
    parser.add_argument("--max-games", type=int, default=6)
    parser.add_argument(
        "--event-type",
        choices=("highlights", "blocks"),
        default="highlights",
        help="Filter clips by event type",
    )
    parser.add_argument(
        "--all-games",
        action="store_true",
        help="Process all games in the selected season (instead of only --max-games)",
    )
    parser.add_argument("--target-date", default=None, help="Target game date, e.g. 2025-12-30")
    parser.add_argument("--target-opponent", default=None, help="Opponent abbreviation, e.g. UTA")
    parser.add_argument("--recent-game-index", type=int, default=None, help="0=most recent game")
    parser.add_argument("--trim-last-seconds", type=int, default=0, help="Trim each clip to last N seconds")
    parser.add_argument("--output-name", default=None, help="Final stitched output filename")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    use_script_settings = args.player_name is None
    if use_script_settings:
        cfg = SCRIPT_SETTINGS
        player_name = cfg.player_name
        max_highlights = max(1, int(cfg.max_highlights))
        season = cfg.season
        event_type = cfg.event_type
        all_games = cfg.all_games
        max_games = cfg.max_games
        target_date = cfg.target_date
        target_opponent = cfg.target_opponent
        recent_game_index = cfg.recent_game_index
        trim_last_seconds = max(0, int(cfg.trim_last_seconds))
        output_name = cfg.output_basename
        downloads_dir = cfg.downloads_dir
        output_dir = cfg.output_dir
        auto_stitch = cfg.auto_stitch
        print("Running with SCRIPT_SETTINGS (no CLI args provided).")
    else:
        player_name = args.player_name
        max_highlights = max(1, int(args.max_highlights))
        season = args.season
        event_type = args.event_type
        all_games = args.all_games
        max_games = args.max_games
        target_date = args.target_date
        target_opponent = args.target_opponent
        recent_game_index = args.recent_game_index
        trim_last_seconds = max(0, int(args.trim_last_seconds or 0))
        output_name = args.output_name
        downloads_dir = "downloads"
        output_dir = "output"
        auto_stitch = True

    if not NBA_API_AVAILABLE:
        print("Warning: nba_api is not installed. Install with: pip install nba_api")
        print("Only fallback NBA.com search will be available.\n")

    print(f"Creating highlight reel for {player_name}...")
    print("=" * 50)

    finder = NBAHighlightsFinder(player_name=player_name, season=season)
    games_limit: Optional[int] = None if all_games else max(1, max_games)
    candidate_games = finder.get_recent_games(max_games=games_limit if not target_date and recent_game_index is None else None)
    if target_date or target_opponent:
        filtered = []
        for game in candidate_games:
            date_ok = True
            opp_ok = True
            if target_date:
                date_ok = _date_matches(target_date, str(game.get("date", "")))
            if target_opponent:
                opp_ok = str(target_opponent).upper() in str(game.get("matchup", "")).upper()
            if date_ok and opp_ok:
                filtered.append(game)
        candidate_games = filtered

    if recent_game_index is not None and candidate_games:
        idx = max(0, int(recent_game_index))
        candidate_games = candidate_games[idx : idx + 1]

    if not candidate_games:
        print("No matching games found for current filters.")
        sys.exit(1)

    # If blocks + selected specific game(s), use BLK context sequence directly.
    highlights: List[Dict] = []
    if event_type == "blocks":
        for game in candidate_games:
            highlights.extend(
                finder._get_context_measure_video_candidates(
                    game_id=game["game_id"],
                    team_id=int(game.get("team_id", 0) or 0),
                    context_measure="BLK",
                )
            )
        # de-dup while preserving order
        seen = set()
        unique = []
        for item in highlights:
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(item)
            if len(unique) >= max_highlights:
                break
        highlights = unique
    else:
        # Fallback to existing generic flow
        highlights = finder.get_highlights(
            max_results=max_highlights,
            max_games=games_limit,
            event_type=event_type,
        )

    if not highlights:
        print("No highlights found.")
        print("Try a different player, another season, or manual mode via stitch_videos.py")
        sys.exit(1)

    downloaded: List[str] = []
    print(f"\nDownloading up to {len(highlights)} clips...")
    for index, highlight in enumerate(highlights, 1):
        source_url = highlight.get("url", "")
        title = highlight.get("title", "Untitled")
        print(f"[{index}/{len(highlights)}] {title}")
        if not source_url:
            continue

        clip_path = download_video_from_source_url(
            source_url=source_url,
            output_filename=f"highlight_{index:03d}.mp4",
            output_dir=downloads_dir,
        )
        if clip_path:
            downloaded.append(clip_path)

        if len(downloaded) >= max_highlights:
            break

    if not downloaded:
        print("\nNo clips were downloaded successfully.")
        print("Potential reasons: DRM, auth restrictions, or unsupported source URLs.")
        sys.exit(1)

    # Optional trim step before stitching.
    if trim_last_seconds > 0:
        print(f"\nTrimming clips to last {trim_last_seconds} seconds...")
        downloaded = trim_last_seconds_of_clips(downloaded, trim_last_seconds, downloads_dir)

    if not auto_stitch:
        print("\nAuto-stitch disabled by settings. Downloaded clips:")
        for clip in downloaded:
            print(clip)
        return

    stitcher = VideoStitcher(output_dir=output_dir)
    safe_name = re.sub(r"[^\w\s-]", "", player_name).strip().replace(" ", "_")
    output_name = output_name or f"{safe_name}_highlight_reel.mp4"

    print(f"\nStitching {len(downloaded)} clips...")
    final = stitcher.stitch_videos(downloaded, output_name)
    if final:
        print("\n✓ Highlight reel created successfully")
        print(f"Output: {final}")
    else:
        print("\n✗ Failed to create highlight reel")


if __name__ == "__main__":
    main()
