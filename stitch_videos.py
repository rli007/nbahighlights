#!/usr/bin/env python3
"""
Simple video stitcher for manually provided video files or URLs.
Use this if the automatic scraper doesn't work with NBA.com.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Optional
import yt_dlp


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
    
    def stitch_videos(self, video_paths: List[str], output_filename: str) -> Optional[str]:
        """
        Stitch multiple videos together using ffmpeg.
        
        Args:
            video_paths: List of paths to video files
            output_filename: Name of the output file
            
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
        
        # Filter out None values and check if files exist
        valid_paths = []
        for v in video_paths:
            if v and os.path.exists(v):
                valid_paths.append(v)
            elif v and (v.startswith('http://') or v.startswith('https://')):
                # It's a URL, we'll download it first
                print(f"Downloading video from URL: {v}")
                downloader = VideoDownloader()
                downloaded = downloader.download_video(v)
                if downloaded:
                    valid_paths.append(downloaded)
        
        if not valid_paths:
            print("Error: No valid video files found")
            return None
        
        output_path = self.output_dir / output_filename
        
        # Create a file list for ffmpeg concat
        concat_file = self.output_dir / "concat_list.txt"
        with open(concat_file, 'w') as f:
            for video_path in valid_paths:
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
                print(f"Copy codec failed, trying with re-encoding...")
                # Try with re-encoding if copy fails
                return self._stitch_with_reencode(valid_paths, output_path, concat_file)
                
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
                '-preset', 'medium',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '192k',
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


def main():
    """Main function to stitch videos."""
    if len(sys.argv) < 3:
        print("Usage: python stitch_videos.py <output_filename> <video1> [video2] [video3] ...")
        print("Example: python stitch_videos.py lebron_reel.mp4 video1.mp4 video2.mp4 video3.mp4")
        print("Or with URLs: python stitch_videos.py reel.mp4 https://example.com/video1.mp4 https://example.com/video2.mp4")
        sys.exit(1)
    
    output_filename = sys.argv[1]
    video_paths = sys.argv[2:]
    
    print(f"Stitching {len(video_paths)} videos into {output_filename}...")
    print("=" * 50)
    
    stitcher = VideoStitcher()
    final_video = stitcher.stitch_videos(video_paths, output_filename)
    
    if final_video:
        print(f"\n✓ Highlight reel created successfully!")
        print(f"  Output: {final_video}")
    else:
        print("\n✗ Failed to create highlight reel")


if __name__ == "__main__":
    main()

