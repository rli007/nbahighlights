# NBA Highlights Reel Creator

A Python tool that uses the [nba_api](https://github.com/swar/nba_api) package to find player data and game information, then locates and stitches video highlights into a highlight reel.

## Features

- 📊 Uses **nba_api** to get player stats and recent game data
- 🔍 Intelligently searches for highlights based on player performance
- 📥 Downloads video clips automatically using yt-dlp
- 🎬 Stitches multiple clips into a single highlight reel using ffmpeg
- 🎯 Filters highlights by player name and game context

## Prerequisites

1. **Python 3.7+**
2. **ffmpeg** - Required for video stitching
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt-get install ffmpeg` or `sudo yum install ffmpeg`
   - Windows: Download from [ffmpeg.org](https://ffmpeg.org/download.html)

## Installation

1. Clone this repository:
```bash
git clone <your-repo-url>
cd nbahighlights
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Automatic Scraping (Primary Method)

Basic usage:
```bash
python nba_highlights.py "LeBron James"
```

Specify maximum number of highlights:
```bash
python nba_highlights.py "Stephen Curry" 15
```

The script will:
1. Use **nba_api** to find the player ID and get recent game statistics
2. Search NBA.com for highlights based on recent games and player performance
3. Download the video clips to the `downloads/` directory
4. Stitch them together into a highlight reel in the `output/` directory

**Note**: The script works without `nba_api` but with limited functionality. For best results, ensure `nba_api` is installed.

### Manual Video Stitching (Alternative Method)

If automatic scraping doesn't work, you can manually provide video files or URLs:

```bash
# With local video files
python stitch_videos.py output_reel.mp4 video1.mp4 video2.mp4 video3.mp4

# With video URLs
python stitch_videos.py output_reel.mp4 https://example.com/video1.mp4 https://example.com/video2.mp4

# Mix of local files and URLs
python stitch_videos.py output_reel.mp4 local_video.mp4 https://example.com/video.mp4
```

## Output

- **Downloaded clips**: `downloads/highlight_001.mp4`, `downloads/highlight_002.mp4`, etc.
- **Final highlight reel**: `output/<player_name>_highlight_reel.mp4`

## Important Notes

⚠️ **Copyright & Legal Considerations**:
- NBA content is protected by copyright
- This tool is for personal use only
- Ensure compliance with NBA.com's Terms of Service
- Do not distribute or monetize the created highlight reels without proper authorization

⚠️ **Technical Limitations**:
- The `nba_api` package provides stats data but not direct video URLs
- Some videos may be DRM-protected and cannot be downloaded
- Video quality depends on what's available on NBA.com
- NBA.com's video structure may change, requiring updates to the video extraction logic
- The script uses a hybrid approach: `nba_api` for data + web scraping for video URLs

## Troubleshooting

**"No highlights found"**:
- The NBA.com website structure may have changed
- Try searching manually on NBA.com to verify highlights exist
- The player name might need to be formatted differently

**"ffmpeg is not installed"**:
- Install ffmpeg using the commands in Prerequisites
- Verify installation with: `ffmpeg -version`

**"No videos were successfully downloaded"**:
- Videos may be DRM-protected
- NBA.com may require authentication
- Try accessing the video URLs manually in a browser

## How It Works

This tool uses a hybrid approach:

1. **nba_api** ([GitHub](https://github.com/swar/nba_api)) - Gets player information, game logs, and statistics
2. **Web Search** - Uses game data to intelligently search NBA.com for relevant highlights
3. **yt-dlp** - Downloads videos from various sources
4. **ffmpeg** - Stitches videos together into a final reel

## Alternative Approaches

If automatic finding doesn't work:
1. Manually collect video URLs from NBA.com
2. Use the `stitch_videos.py` script with manual URLs
3. Use screen recording software (OBS Studio, QuickTime) to record clips
4. Use video editing software (DaVinci Resolve, Adobe Premiere) to stitch manually

## License

This project is for educational and personal use only. Respect copyright laws and NBA.com's Terms of Service.
