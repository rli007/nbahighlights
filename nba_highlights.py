#!/usr/bin/env python3
"""
NBA Highlights Reel Creator
Uses nba_api to get player data and game events, then finds and stitches video highlights.
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup
import yt_dlp

try:
    from nba_api.stats.endpoints import (
        playergamelog, 
        playbyplayv2, 
        commonplayerinfo,
        scoreboardv2
    )
    from nba_api.stats.static import players
    from nba_api.live.nba.endpoints import scoreboard
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    print("Warning: nba_api not installed. Install with: pip install nba_api")


class NBAHighlightsFinder:
    """Finds player highlights using nba_api and video search."""
    
    BASE_URL = "https://www.nba.com"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    def __init__(self, player_name: str):
        self.player_name = player_name
        self.player_id = None
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.highlights = []
        
        if NBA_API_AVAILABLE:
            self._find_player_id()
    
    def _find_player_id(self) -> Optional[str]:
        """Find player ID from nba_api."""
        try:
            all_players = players.get_players()
            for player in all_players:
                full_name = f"{player['first_name']} {player['last_name']}"
                if self.player_name.lower() in full_name.lower() or full_name.lower() in self.player_name.lower():
                    self.player_id = player['id']
                    print(f"Found player: {full_name} (ID: {self.player_id})")
                    return self.player_id
            
            print(f"Warning: Could not find player ID for '{self.player_name}'")
            return None
        except Exception as e:
            print(f"Error finding player ID: {e}")
            return None
    
    def get_recent_games(self, days_back: int = 30, max_games: int = 10) -> List[Dict]:
        """
        Get recent games for the player using nba_api.
        
        Args:
            days_back: Number of days to look back
            max_games: Maximum number of games to retrieve
            
        Returns:
            List of game dictionaries
        """
        if not NBA_API_AVAILABLE or not self.player_id:
            return []
        
        try:
            # Get player game log
            game_log = playergamelog.PlayerGameLog(
                player_id=self.player_id,
                season='2024-25',  # Current season - may need adjustment
                season_type_all_star='Regular Season'
            )
            
            games_df = game_log.get_data_frames()[0]
            
            # Get recent games
            recent_games = []
            for idx, row in games_df.head(max_games).iterrows():
                game_date = row['GAME_DATE']
                game_id = row['MATCHUP'].split(' ')[-1]  # Extract game ID if available
                
                recent_games.append({
                    'game_id': game_id,
                    'date': game_date,
                    'matchup': row['MATCHUP'],
                    'pts': row['PTS'],
                    'reb': row['REB'],
                    'ast': row['AST'],
                })
            
            return recent_games
            
        except Exception as e:
            print(f"Error getting recent games: {e}")
            # Try without specifying season
            try:
                game_log = playergamelog.PlayerGameLog(
                    player_id=self.player_id,
                    season='2023-24'
                )
                games_df = game_log.get_data_frames()[0]
                recent_games = []
                for idx, row in games_df.head(max_games).iterrows():
                    recent_games.append({
                        'game_id': '',
                        'date': row['GAME_DATE'],
                        'matchup': row['MATCHUP'],
                        'pts': row['PTS'],
                        'reb': row['REB'],
                        'ast': row['AST'],
                    })
                return recent_games
            except Exception as e2:
                print(f"Error getting games (fallback): {e2}")
                return []
    
    def get_highlight_events(self, game_id: str = None) -> List[Dict]:
        """
        Get highlight-worthy events from play-by-play data.
        
        Args:
            game_id: Specific game ID to analyze
            
        Returns:
            List of highlight events
        """
        if not NBA_API_AVAILABLE or not self.player_id:
            return []
        
        highlight_events = []
        
        try:
            # Get recent games if no specific game_id
            if not game_id:
                games = self.get_recent_games(max_games=5)
                if not games:
                    return []
                # Use the most recent game
                # Note: We'd need the actual game ID format from scoreboard
                pass
            
            # Get play-by-play data
            # Note: This requires the actual game ID format
            # For now, we'll use a different approach
            
        except Exception as e:
            print(f"Error getting highlight events: {e}")
        
        return highlight_events
    
    def search_video_highlights(self, max_results: int = 20) -> List[Dict]:
        """
        Search for video highlights on NBA.com using player name and game data.
        
        Args:
            max_results: Maximum number of highlights to retrieve
            
        Returns:
            List of highlight dictionaries with title, description, and video URL
        """
        print(f"Searching for highlights of {self.player_name}...")
        
        video_links = []
        
        # Method 1: Use nba_api to get recent games, then search for videos
        if NBA_API_AVAILABLE and self.player_id:
            games = self.get_recent_games(max_games=5)
            print(f"Found {len(games)} recent games")
            
            # Search for videos related to recent games
            for game in games:
                search_terms = [
                    f"{self.player_name} {game['matchup']}",
                    f"{self.player_name} {game['date']}",
                    f"{self.player_name} {game['pts']} points"
                ]
                
                for term in search_terms:
                    video_urls = self._search_nba_videos(term)
                    video_links.extend(video_urls)
                    if len(video_links) >= max_results:
                        break
                
                if len(video_links) >= max_results:
                    break
        
        # Method 2: Direct search on NBA.com
        if len(video_links) < max_results:
            search_url = f"{self.BASE_URL}/search?q={quote(self.player_name + ' highlights')}"
            additional_videos = self._search_nba_videos_from_url(search_url)
            video_links.extend(additional_videos)
        
        # Remove duplicates and limit
        seen_urls = set()
        unique_links = []
        for link in video_links:
            if link['url'] not in seen_urls:
                seen_urls.add(link['url'])
                unique_links.append(link)
                if len(unique_links) >= max_results:
                    break
        
        self.highlights = unique_links
        print(f"Found {len(self.highlights)} unique highlights")
        return self.highlights
    
    def _search_nba_videos(self, search_term: str) -> List[Dict]:
        """Search NBA.com for videos matching search term."""
        search_url = f"{self.BASE_URL}/search?q={quote(search_term)}"
        return self._search_nba_videos_from_url(search_url)
    
    def _search_nba_videos_from_url(self, url: str) -> List[Dict]:
        """Extract video links from NBA.com search page."""
        video_links = []
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for video/article links
            for link in soup.find_all('a', href=True):
                href = link.get('href', '')
                text = link.get_text(strip=True)
                
                # Check if it's a video/highlight link
                if any(keyword in href.lower() for keyword in ['video', 'highlight', 'play', 'watch']):
                    if self.player_name.lower() in text.lower() or 'highlight' in text.lower():
                        full_url = urljoin(self.BASE_URL, href)
                        video_links.append({
                            'url': full_url,
                            'title': text,
                            'description': ''
                        })
            
            # Look for embedded video players
            for player in soup.find_all(['video', 'iframe']):
                src = player.get('src', '') or player.get('data-src', '')
                if src and any(domain in src for domain in ['nba.com', 'youtube', 'vimeo']):
                    video_links.append({
                        'url': src,
                        'title': f"{self.player_name} Highlight",
                        'description': ''
                    })
            
        except requests.RequestException as e:
            print(f"Error searching NBA.com: {e}")
        
        return video_links
    
    def get_video_urls_from_page(self, page_url: str) -> List[str]:
        """
        Extract video URLs from a specific NBA.com page.
        
        Args:
            page_url: URL of the page containing highlights
            
        Returns:
            List of video URLs
        """
        try:
            response = self.session.get(page_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            video_urls = []
            
            # Look for video sources
            for video_tag in soup.find_all('video'):
                sources = video_tag.find_all('source')
                for source in sources:
                    src = source.get('src', '')
                    if src:
                        video_urls.append(urljoin(page_url, src))
            
            # Look for iframe embeds
            for iframe in soup.find_all('iframe'):
                src = iframe.get('src', '')
                if src:
                    video_urls.append(src)
            
            # Look for data attributes that might contain video URLs
            for element in soup.find_all(attrs={'data-video-url': True}):
                video_urls.append(element['data-video-url'])
            
            return video_urls
            
        except requests.RequestException as e:
            print(f"Error fetching page {page_url}: {e}")
            return []


class VideoDownloader:
    """Downloads videos from URLs."""
    
    def __init__(self, output_dir: str = "downloads"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def download_video(self, url: str, filename: Optional[str] = None) -> Optional[str]:
        """
        Download a video from a URL using yt-dlp.
        
        Args:
            url: Video URL
            filename: Optional output filename
            
        Returns:
            Path to downloaded video file, or None if download failed
        """
        if filename is None:
            filename = f"video_{hash(url) % 10000}.mp4"
        
        output_path = self.output_dir / filename
        
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(output_path.with_suffix('')),
            'quiet': False,
            'no_warnings': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                
            # Find the actual downloaded file (yt-dlp may add extension)
            downloaded_files = list(self.output_dir.glob(f"{output_path.stem}*"))
            if downloaded_files:
                return str(downloaded_files[0])
            
            return None
            
        except Exception as e:
            print(f"Error downloading video from {url}: {e}")
            return None


class VideoStitcher:
    """Stitches multiple videos together into a highlight reel."""
    
    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def check_ffmpeg(self) -> bool:
        """Check if ffmpeg is installed."""
        try:
            subprocess.run(['ffmpeg', '-version'], 
                         capture_output=True, 
                         check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def stitch_videos(self, video_paths: List[str], output_filename: str, 
                     transition_duration: float = 0.5) -> Optional[str]:
        """
        Stitch multiple videos together using ffmpeg.
        
        Args:
            video_paths: List of paths to video files
            output_filename: Name of the output file
            transition_duration: Duration of crossfade transition between clips (seconds)
            
        Returns:
            Path to the stitched video, or None if stitching failed
        """
        if not self.check_ffmpeg():
            print("Error: ffmpeg is not installed. Please install ffmpeg first.")
            print("Install with: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)")
            return None
        
        if not video_paths:
            print("Error: No video files to stitch")
            return None
        
        # Filter out None values
        video_paths = [v for v in video_paths if v and os.path.exists(v)]
        
        if not video_paths:
            print("Error: No valid video files found")
            return None
        
        output_path = self.output_dir / output_filename
        
        # Create a file list for ffmpeg concat
        concat_file = self.output_dir / "concat_list.txt"
        with open(concat_file, 'w') as f:
            for video_path in video_paths:
                f.write(f"file '{os.path.abspath(video_path)}'\n")
        
        try:
            # Use ffmpeg to concatenate videos
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c', 'copy',  # Copy codec (fast, no re-encoding)
                '-y',  # Overwrite output file
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully created highlight reel: {output_path}")
                concat_file.unlink()  # Clean up
                return str(output_path)
            else:
                print(f"Error stitching videos: {result.stderr}")
                # Try with re-encoding if copy fails
                return self._stitch_with_reencode(video_paths, output_path, concat_file)
                
        except Exception as e:
            print(f"Error during video stitching: {e}")
            return None
    
    def _stitch_with_reencode(self, video_paths: List[str], output_path: Path, 
                              concat_file: Path) -> Optional[str]:
        """Fallback method with re-encoding."""
        try:
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-y',
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully created highlight reel (with re-encoding): {output_path}")
                concat_file.unlink()
                return str(output_path)
            else:
                print(f"Error stitching videos with re-encoding: {result.stderr}")
                return None
                
        except Exception as e:
            print(f"Error during re-encoding: {e}")
            return None


def main():
    """Main function to create a highlight reel."""
    if len(sys.argv) < 2:
        print("Usage: python nba_highlights.py <player_name> [max_highlights]")
        print("Example: python nba_highlights.py 'LeBron James' 10")
        print("\nNote: Install nba_api for better results: pip install nba_api")
        sys.exit(1)
    
    player_name = sys.argv[1]
    max_highlights = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    if not NBA_API_AVAILABLE:
        print("Warning: nba_api not installed. Install with: pip install nba_api")
        print("The script will still work but with limited functionality.\n")
    
    print(f"Creating highlight reel for {player_name}...")
    print("=" * 50)
    
    # Step 1: Find highlights using nba_api and search
    finder = NBAHighlightsFinder(player_name)
    highlights = finder.search_video_highlights(max_results=max_highlights)
    
    if not highlights:
        print("No highlights found. The scraper may need to be updated for NBA.com's current structure.")
        print("You may need to manually provide video URLs or use the NBA API if available.")
        sys.exit(1)
    
    # Step 2: Download videos
    downloader = VideoDownloader()
    downloaded_videos = []
    
    print(f"\nDownloading {len(highlights)} videos...")
    for i, highlight in enumerate(highlights, 1):
        print(f"Downloading {i}/{len(highlights)}: {highlight.get('title', 'Untitled')}")
        video_url = highlight.get('url', '')
        
        if not video_url:
            continue
        
        # Try to get actual video URL from the page
        if 'nba.com' in video_url:
            actual_urls = finder.get_video_urls_from_page(video_url)
            if actual_urls:
                video_url = actual_urls[0]
        
        video_path = downloader.download_video(
            video_url, 
            filename=f"highlight_{i:03d}.mp4"
        )
        
        if video_path:
            downloaded_videos.append(video_path)
    
    if not downloaded_videos:
        print("\nNo videos were successfully downloaded.")
        print("This might be because:")
        print("1. NBA.com uses DRM-protected videos")
        print("2. The video URLs require authentication")
        print("3. The video format is not supported")
        print("\nYou may need to manually download videos or use screen recording.")
        sys.exit(1)
    
    # Step 3: Stitch videos together
    print(f"\nStitching {len(downloaded_videos)} videos together...")
    stitcher = VideoStitcher()
    
    safe_player_name = re.sub(r'[^\w\s-]', '', player_name).strip().replace(' ', '_')
    output_filename = f"{safe_player_name}_highlight_reel.mp4"
    
    final_video = stitcher.stitch_videos(downloaded_videos, output_filename)
    
    if final_video:
        print(f"\n✓ Highlight reel created successfully!")
        print(f"  Output: {final_video}")
    else:
        print("\n✗ Failed to create highlight reel")


if __name__ == "__main__":
    main()
