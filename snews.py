#!/usr/bin/env python3
import curses
import traceback
import json
import os
import re
import threading
import traceback
import webbrowser
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import List, Set, Dict, Optional
import requests
from bs4 import BeautifulSoup
import feedparser
import pytz
import sqlite3
import hashlib
from functools import lru_cache
import urllib.parse
import subprocess
import arxiv
from dataclasses import dataclass
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import textwrap
import tempfile
import pdfplumber
import requests
import os
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup
import requests
import urllib.parse
import cloudscraper
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

os.chdir('/home/garuda/scripts/snews')

# Constants
CACHE_FILE = Path.home() / '.cache' / 'brutalist_links.json'
HISTORY_FILE = Path.home() / '.cache' / 'brutalist_history.json'
BOOKMARKS_FILE = Path.home() / '.cache' / 'brutalist_bookmarks.json'
RSS_DB_FILE = Path.home() / '.cache' / 'brutalist_rss.db'
FEEDS_FILE = Path.home() / '.cache' / 'brutalist_feeds.json'
OUTPUT_DIR = Path.home() / 'outputs'
WINDOW_SIZE = 50
UPDATE_INTERVAL = 300  # 5 minutes
MAX_CACHE_SIZE = 1000

class ColorScheme:
    def __init__(self):
        self.title_fg = curses.COLOR_YELLOW
        self.title_bg = -1
        self.details_fg = curses.COLOR_WHITE
        self.details_bg = -1
        self.highlight_fg = curses.COLOR_BLACK
        self.highlight_bg = curses.COLOR_YELLOW
        
    def setup_colors(self):
        """Initialize color pairs for curses."""
        curses.init_pair(1, self.title_fg, self.title_bg)      # Title
        curses.init_pair(2, self.details_fg, self.details_bg)  # Details
        curses.init_pair(3, self.highlight_fg, self.highlight_bg)  # Selected
        
    def cycle_title_color(self):
        """Cycle through available colors for titles."""
        colors = [curses.COLOR_YELLOW, curses.COLOR_GREEN, curses.COLOR_CYAN, 
                 curses.COLOR_WHITE, curses.COLOR_MAGENTA, curses.COLOR_RED, curses.COLOR_BLUE]
        self.title_fg = colors[(colors.index(self.title_fg) + 1) % len(colors)]
        self.setup_colors()

class FeedManager:
    def __init__(self):
        self.feeds: Dict[str, str] = self.load_feeds()
        self._local = threading.local()
        self.stop_flag = threading.Event()
        self.update_thread: Optional[threading.Thread] = None
        self._init_db()

    def _init_db(self):
        """Initialize the database file and schema."""
        RSS_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(RSS_DB_FILE))
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS articles
                    (id TEXT PRIMARY KEY, 
                     title TEXT, 
                     url TEXT, 
                     source TEXT,
                     feed_url TEXT,
                     published TEXT,
                     content TEXT,
                     read INTEGER DEFAULT 0,
                     favorite INTEGER DEFAULT 0)''')
        conn.commit()
        conn.close()

    @property
    def db_conn(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(str(RSS_DB_FILE))
        return self._local.conn

    def close_connections(self):
        """Close database connections for cleanup."""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            del self._local.conn

    def load_feeds(self) -> Dict[str, str]:
        """Load RSS feeds from config file."""
        try:
            if FEEDS_FILE.exists():
                with open(FEEDS_FILE) as f:
                    return json.load(f)
            return {}
        except Exception:
            return {}

    @lru_cache(maxsize=MAX_CACHE_SIZE)
    def get_feed_content(self, url: str) -> Optional[feedparser.FeedParserDict]:
        """Fetch and parse RSS feed with caching."""
        try:
            # Create client for arxiv requests
            client = arxiv.Client()
            # Create search object
            search = arxiv.Search(
                query="all:*",
                max_results=1  # We just need one result to test
            )
            # Use client.results() instead of search.results()
            next(client.results(search), None)  # Test the connection
            return feedparser.parse(url)
        except Exception:
            return None

    def add_feed(self, name: str, url: str) -> bool:
        """Add a new RSS feed."""
        try:
            feed = self.get_feed_content(url)
            if feed and feed.entries:
                self.feeds[name] = url
                self.save_feeds()
                self.update_feed(name, url)
                return True
            return False
        except Exception:
            return False

    def remove_feed(self, name: str):
        """Remove an RSS feed."""
        if name in self.feeds:
            del self.feeds[name]
            self.save_feeds()

    def update_feed(self, name: str, url: str):
        """Update articles from a specific feed."""
        feed = self.get_feed_content(url)
        if not feed:
            return

        conn = self.db_conn
        c = conn.cursor()
        
        for entry in feed.entries:
            article_id = hashlib.md5(entry.link.encode()).hexdigest()
            
            # Extract content
            content = ""
            if hasattr(entry, 'content'):
                content = entry.content[0].value
            elif hasattr(entry, 'summary'):
                content = entry.summary
                
            # Get publication date
            published = datetime.now(pytz.UTC).isoformat()
            if hasattr(entry, 'published_parsed'):
                published = datetime.fromtimestamp(
                    entry.published_parsed[0], pytz.UTC).isoformat()
            
            c.execute('''INSERT OR IGNORE INTO articles 
                        (id, title, url, source, feed_url, published, content) 
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (article_id, entry.title, entry.link, name, url, published, content))
        
        conn.commit()

    def update_all_feeds(self):
        """Update all RSS feeds."""
        for name, url in self.feeds.items():
            try:
                self.update_feed(name, url)
            except Exception as e:
                print(f"Error updating feed {name}: {e}")

    def start_auto_update(self):
        """Start automatic feed updates."""
        def update_loop():
            while not self.stop_flag.is_set():
                try:
                    self.update_all_feeds()
                except Exception as e:
                    print(f"Error in update loop: {e}")
                finally:
                    self.close_connections()
                self.stop_flag.wait(UPDATE_INTERVAL)

        self.stop_flag.clear()
        self.update_thread = threading.Thread(target=update_loop, daemon=True)
        self.update_thread.start()

    def stop_auto_update(self):
        """Stop automatic feed updates."""
        if self.update_thread:
            self.stop_flag.set()
            self.update_thread.join()
            self.update_thread = None
        self.close_connections()

    def save_feeds(self):
        """Save RSS feeds to config file."""
        try:
            FEEDS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(FEEDS_FILE, 'w') as f:
                json.dump(self.feeds, f, indent=2)
        except Exception as e:
            print(f"Error saving feeds: {e}")

    def __del__(self):
        """Cleanup when the object is destroyed."""
        self.stop_auto_update()
        self.close_connections()

@dataclass
class ArchiveItem:
    """Represents an item from Anna's Archive."""
    title: str
    author: str
    year: str
    format: str
    size: str
    language: str
    url: str
    summary: Optional[str] = None

class AnnasArchiveManager:
    """Manages searches and content retrieval from Anna's Archive."""
    
    BASE_URL = "https://annas-archive.org/search"
    
    def __init__(self):
        self.current_search = None
        self.current_results: List[ArchiveItem] = []
        
    def search(self, query: str) -> List[ArchiveItem]:
        """Search Anna's Archive and return top 10 results."""
        try:
            # Build search URL
            encoded_query = urllib.parse.quote(query)
            url = f"{self.BASE_URL}?q={encoded_query}"
            
            # Make request with a desktop user agent
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Parse results
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Find all search result entries - they are in h-[110px] flex flex-col justify-center containers
            items = soup.find_all('div', class_=lambda x: x and 'h-[110px]' in x and 'flex-col' in x)[:10]

            for item in items:
                try:
                    # Find the title in max-lg:line-clamp-[2] class
                    title_elem = item.find('div', class_=lambda x: x and 'max-lg:line-clamp-[2]' in x)
                    if not title_elem:
                        continue

                    title = title_elem.get_text(strip=True)
                    
                    # Get the link (should be a parent element)
                    link_elem = item.find('a', href=True)
                    if not link_elem:
                        continue
                        
                    url = 'https://annas-archive.org' + link_elem['href']

                    # The metadata is usually in the text elements near the title
                    meta_text = item.get_text(' ', strip=True)
                    
                    # Initialize metadata fields
                    author = "Unknown"
                    year = "Unknown"
                    format = "Unknown"
                    size = "Unknown"
                    language = "English"  # Default
                    
                    # Try to extract metadata
                    # Year is often in a format like ", 1994"
                    year_match = re.search(r',\s*(\d{4})', meta_text)
                    if year_match:
                        year = year_match.group(1)
                        
                    # Size is often followed by MB or GB
                    size_match = re.search(r'(\d+(?:\.\d+)?\s*[MG]B)', meta_text)
                    if size_match:
                        size = size_match.group(1)
                        
                    # Format is often PDF, EPUB, etc.
                    format_match = re.search(r'\b(PDF|EPUB|MOBI|AZW3|DOC|DOCX)\b', meta_text, re.IGNORECASE)
                    if format_match:
                        format = format_match.group(1).upper()
                        
                    # Try to find author (often before the year)
                    if year_match:
                        author_text = meta_text.split(year_match.group(0))[0].strip()
                        if author_text and author_text != title:
                            author = author_text

                    archive_item = ArchiveItem(
                        title=title,
                        author=author,
                        year=year,
                        format=format,
                        size=size,
                        language=language,
                        url=url
                    )
                    results.append(archive_item)
                    
                except Exception as e:
                    print(f"Error parsing item: {e}")
                    continue
            
            self.current_results = results
            return results
            
        except Exception as e:
            print(f"Error searching Anna's Archive: {e}")
            return []

    def get_item_summary(self, item: ArchiveItem) -> str:
        """Fetch and parse detailed information about an item."""
        try:
            if not item.url:
                return "No URL available for this item."
                
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(item.url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            summary_sections = []
            
            # Look for description elements - usually in text-sm or similar classes
            description_divs = soup.find_all('div', class_=lambda x: x and 'text-sm' in x)
            for div in description_divs:
                text = div.get_text(strip=True)
                if len(text) > 50:  # Only include substantial text
                    summary_sections.append(text)
                    
            # Also look for any detailed metadata
            meta_divs = soup.find_all('div', class_=lambda x: x and 'metadata' in x)
            for div in meta_divs:
                text = div.get_text(strip=True)
                if text:
                    summary_sections.append(text)

            if summary_sections:
                summary = "\n\n".join(summary_sections)
            else:
                summary = "No detailed description available."
                
            item.summary = summary
            return summary
            
        except Exception as e:
            return f"Error fetching item details: {e}"

    def handle_download_key(self):
        """Handle the 'd' key press for downloads in the search results view."""
        if not self.archive_manager.current_results:
            return

        item = self.archive_manager.current_results[self.selected_index]

        # Start download without leaving curses mode
        self.status_message = f"Downloading {item.title}..."
        self.draw_search_results()  # Refresh display with new status

        try:
            filepath = self.download_item(item)
            if filepath:
                self.status_message = f"Download complete: {os.path.basename(filepath)}"
            else:
                self.status_message = "Download failed or was cancelled"
        except Exception as e:
            self.status_message = f"Download error: {str(e)}"

        self.draw_search_results()  # Refresh display with final status

    def download_item(self, item: ArchiveItem, temp_only=False):
        """Enhanced download function with progress tracking and better error handling."""
        try:
            # First fetch the download page
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(item.url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Direct to Slow Partner Server #3 (no waitlist)
            slow_downloads = soup.find('h3', string=lambda x: x and 'Slow downloads' in x)
            if not slow_downloads:
                raise Exception("Could not find slow downloads section")
                
            options_list = slow_downloads.find_next('ul', class_='list-disc')
            if not options_list:
                raise Exception("Could not find download options")
                
            # Find Server #3 specifically
            option3_item = None
            for li in options_list.find_all('li'):
                if 'Server #3' in li.text and 'no waitlist' in li.text.lower():
                    option3_item = li
                    break
                    
            if not option3_item:
                raise Exception("Could not find Slow Partner Server #3")
                
            # Get the download link
            download_link = option3_item.find('a', href=lambda x: x and '/download/' in x)
            if not download_link:
                raise Exception("Could not find download link")
            
            download_url = download_link['href']
            if not download_url.startswith('http'):
                download_url = 'https://annas-archive.org' + download_url
            
            # Generate filename
            filename = f"{item.title[:50]}_{item.size}.{item.format.lower()}"
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.')).strip()
            
            # Set up download location
            if temp_only:
                import tempfile
                temp_dir = tempfile.mkdtemp()
                filepath = os.path.join(temp_dir, filename)
            else:
                filepath = os.path.join(os.getcwd(), filename)
            
            # Prepare for download with progress tracking
            response = requests.get(download_url, headers=headers, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            
            # Initialize progress tracker
            height, _ = self.stdscr.getmaxyx()
            progress = DownloadProgress(total_size, self.stdscr, height - 6)
            
            # Download with progress updates
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    # Check for cancel
                    if self.stdscr.getch() == ord('q'):
                        raise KeyboardInterrupt("Download cancelled by user")
                        
                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))
            
            self.status_message = f"Download complete: {filename}"
            return filepath
            
        except KeyboardInterrupt as e:
            self.status_message = str(e)
            # Clean up partial download
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
            return None
            
        except Exception as e:
            self.status_message = f"Download error: {str(e)}"
            return None

    def process_item(self, item: ArchiveItem):
        """Download, summarize, and clean up an item."""
        try:
            self.status_message = "Downloading file..."
            filepath = self.download_item(item, temp_only=True)
            if not filepath:
                return

            self.status_message = "Processing content..."

            # Extract text based on file format
            content = ""
            if item.format.lower() == 'pdf':
                with pdfplumber.open(filepath) as pdf:
                    for page in pdf.pages[:5]:  # First 5 pages
                        content += page.extract_text() + "\n"
            elif item.format.lower() in ['epub', 'mobi']:
                # Basic text extraction - you might want to use a specialized epub reader
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

            # Format content for summary
            formatted_text = f"""Title: {item.title}
Author: {item.author}
Format: {item.format}
Size: {item.size}
Language: {item.language}

Summary/Preview:
{content[:2000]}...
"""

            # Generate PDF summary
            try:
                subprocess.run(
                    ['/usr/local/bin/wpdf', formatted_text],
                    capture_output=True,
                    text=True,
                    check=True
                )
                self.status_message = "Summary PDF generated successfully!"
            except subprocess.CalledProcessError as e:
                self.status_message = f"Error generating summary PDF: {e}"

            # Clean up
            if os.path.exists(filepath):
                os.remove(filepath)
            if os.path.dirname(filepath) != os.getcwd():
                os.rmdir(os.path.dirname(filepath))

        except Exception as e:
            self.status_message = f"Processing error: {str(e)}"

class AnnasArchiveViewer:
    """Terminal UI for searching and viewing Anna's Archive content."""

    def __init__(self, stdscr, colors: ColorScheme):
        self.stdscr = stdscr
        self.colors = colors
        self.archive_manager = AnnasArchiveManager()
        self.selected_index = 0
        self.status_message = ""
        self.current_view = 'search'  # 'search' or 'detail'

    def run(self):
        """Main run loop for Anna's Archive browser."""
        while True:
            if not self.archive_manager.current_results:
                # Show search prompt
                self.stdscr.clear()
                self.stdscr.addstr(0, 0, "Search Anna's Archive: ")
                curses.echo()
                curses.curs_set(1)
                query = self.stdscr.getstr().decode('utf-8').strip()
                curses.noecho()
                curses.curs_set(0)
                
                if not query:
                    break
                    
                self.status_message = "Searching..."
                self.stdscr.refresh()
                results = self.archive_manager.search(query)
                if results:
                    self.status_message = f"Found {len(results)} results"
                else:
                    self.status_message = "No results found"
                self.selected_index = 0
            
            self.draw_search_results()
            key = self.stdscr.getch()
            
            if key == ord('q'):
                break
            elif key == ord('/'):
                self.archive_manager.current_results = []
                continue
            elif key in [ord('j'), curses.KEY_DOWN]:
                self.selected_index = min(self.selected_index + 1, 
                                       len(self.archive_manager.current_results) - 1)
            elif key in [ord('k'), curses.KEY_UP]:
                self.selected_index = max(self.selected_index - 1, 0)
            elif key in [ord('\n'), curses.KEY_ENTER, 10]:
                if self.archive_manager.current_results:
                    item = self.archive_manager.current_results[self.selected_index]
                    self.show_item_details(item)
            elif key == ord('d'):  # Download file
                if self.archive_manager.current_results:
                    item = self.archive_manager.current_results[self.selected_index]
                    filepath = self.handle_download(item)
                    if filepath:
                        self.draw_search_results()  # Refresh display after download

    def draw_search_results(self):
        """Draw the search results list with improved formatting."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        
        # Draw header with double lines
        header = "═══ Anna's Archive Search Results ═══"
        self.stdscr.attron(curses.color_pair(1))
        self.stdscr.addstr(0, (width - len(header)) // 2, header)
        self.stdscr.attroff(curses.color_pair(1))
        self.stdscr.addstr(1, 0, "─" * (width-1))
        
        # Draw results
        start_y = 2
        for i, item in enumerate(self.archive_manager.current_results):
            if start_y + i * 4 >= height - 3:  # Leave room for status bar
                break
            
            # Calculate y positions for each line
            title_y = start_y + i * 4
            meta_y = title_y + 1
            details_y = meta_y + 1
            spacer_y = details_y + 1
            
            # Highlight entire item if selected
            if i == self.selected_index:
                self.stdscr.attron(curses.color_pair(3))
            
            # Format title line
            title_prefix = f"{i+1}. "
            title_space = width - len(title_prefix) - 2
            title = item.title[:title_space] + ("..." if len(item.title) > title_space else "")
            
            # Format metadata line
            author_str = f"Author: {item.author}" if item.author != "Unknown" else ""
            year_str = f"Year: {item.year}" if item.year != "Unknown" else ""
            metadata = f"  {author_str}  {year_str}".strip()
            
            # Format details line
            format_str = f"Format: {item.format}"
            size_str = f"Size: {item.size}"
            lang_str = f"Language: {item.language}"
            details = f"  {format_str}  |  {size_str}  |  {lang_str}"
            
            try:
                # Draw title with proper highlighting
                self.stdscr.attron(curses.color_pair(1))  # Title color
                if i == self.selected_index:
                    self.stdscr.attron(curses.A_BOLD)
                self.stdscr.addstr(title_y, 2, title_prefix + title)
                if i == self.selected_index:
                    self.stdscr.attroff(curses.A_BOLD)
                self.stdscr.attroff(curses.color_pair(1))
                
                # Draw metadata and details
                self.stdscr.attron(curses.color_pair(2))  # Details color
                self.stdscr.addstr(meta_y, 4, metadata[:width-6])
                self.stdscr.addstr(details_y, 4, details[:width-6])
                self.stdscr.attroff(curses.color_pair(2))
                
                # Draw separator line
                if spacer_y < height - 2:  # Don't draw separator if it would overlap with status bar
                    self.stdscr.addstr(spacer_y, 2, "─" * (width-4))
                    
            except curses.error:
                pass
            
            if i == self.selected_index:
                self.stdscr.attroff(curses.color_pair(3))

        # Status bar at bottom
        if self.status_message:
            try:
                self.stdscr.attron(curses.A_REVERSE)
                status = self.status_message[:width-1]
                self.stdscr.addstr(height-1, 0, status.ljust(width-1))
                self.stdscr.attroff(curses.A_REVERSE)
            except curses.error:
                pass
        else:
            # Default controls
            status = " ↑/↓: Navigate | ENTER: View Details | d: Download | p: Process & Summarize | /: New Search | q: Back "
            try:
                self.stdscr.attron(curses.A_REVERSE)
                self.stdscr.addstr(height-1, 0, status.center(width-1))
                self.stdscr.attroff(curses.A_REVERSE)
            except curses.error:
                pass

        self.stdscr.refresh()

    def handle_download(self, item):
        """Handle the download process for an item."""
        try:
            # Keep terminal mode
            self.status_message = f"Accessing download page for: {item.title}"
            self.draw_search_results()


            # Set up Selenium options
            chrome_options = Options()
            chrome_options.add_argument("--headless")  # Run in headless mode
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--window-size=1920,1080")

            # Get the ChromeDriver that matches your Chrome version
            from selenium.webdriver.chrome.service import Service as ChromeService
            from webdriver_manager.chrome import ChromeDriverManager

            # Install ChromeDriver and create service
            service = ChromeService(ChromeDriverManager().install())

            # Create the driver with our options and service
            driver = webdriver.Chrome(service=service, options=chrome_options)

            # First visit the detail page
            self.status_message = "Fetching detail page..."
            self.draw_search_results()

            driver.get(item.url)

            # Wait for the downloads section to be present
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//h3[contains(text(), 'Slow downloads')]"))
                )
            except:
                self.status_message = "Error: Downloads section did not appear"
                self.draw_search_results()
                time.sleep(2)
                driver.quit()
                return

            # Parse the page source after it's fully loaded
            soup = BeautifulSoup(driver.page_source, 'html.parser')

            # Look for 'Slow downloads' section
            slow_downloads = soup.find('h3', string=lambda x: x and 'Slow downloads' in x)
            if not slow_downloads:
                self.status_message = "Error: Could not find 'Slow downloads' section on page"
                self.draw_search_results()
                time.sleep(2)
                driver.quit()
                return None

            options_list = slow_downloads.find_next('ul', class_='list-disc')
            if not options_list:
                self.status_message = "Error: Could not find download options list"
                self.draw_search_results()
                time.sleep(2)
                driver.quit()
                return None

            # Find Option #3 specifically
            download_link = None
            for li in options_list.find_all('li'):
                link = li.find('a', href=lambda x: x and '/download/' in x)
                if link and 'Option #3' in link.text:
                    download_link = link
                    break

            if not download_link:
                self.status_message = "Error: Could not find Option #3 download link"
                self.draw_search_results()
                time.sleep(2)
                driver.quit()
                return None

            download_url = download_link['href']
            if not download_url.startswith('http'):
                download_url = 'https://annas-archive.org' + download_url

            # Pass the scraper instance to download_item
            filepath = self.download_item(item, download_url)

            if filepath:
                self.status_message = f"Download complete: {os.path.basename(filepath)}"
            else:
                self.status_message = "Download failed or was cancelled"

        except Exception as e:
            self.status_message = f"Download error: {str(e)}"
            if 'driver' in locals():
                driver.save_screenshot("error_screenshot.png")
        finally:
            if 'driver' in locals():
                driver.quit()

        self.draw_search_results()  # Refresh display with final status

    def download_item(self, item: ArchiveItem, download_url, temp_only=False):
        """Download with cloudscraper for potential anti-bot bypass."""
        try:
            # Generate filename
            filename = f"{item.title[:50]}_{item.size}.{item.format.lower()}"
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.')).strip()

            # Set up download location
            if temp_only:
                import tempfile
                temp_dir = tempfile.mkdtemp()
                filepath = os.path.join(temp_dir, filename)
            else:
                filepath = os.path.join(os.getcwd(), filename)

            # Prepare for download with progress tracking
            self.status_message = f"Starting download of {filename}..."
            self.draw_search_results()

            response = requests.get(download_url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192

            # Initialize progress tracker
            height, _ = self.stdscr.getmaxyx()
            progress = DownloadProgress(total_size, self.stdscr, height - 6)

            # Download with progress updates
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    # Check for cancel
                    if self.stdscr.getch() == ord('q'):
                        raise KeyboardInterrupt("Download cancelled by user")

                    if chunk:
                        f.write(chunk)
                        progress.update(len(chunk))

            self.status_message = f"Download complete: {filename}"
            return filepath

        except KeyboardInterrupt as e:
            self.status_message = str(e)
            # Clean up partial download
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
            return None

        except Exception as e:
            self.status_message = f"Download error: {str(e)}"
            return None


    def show_item_details(self, item: ArchiveItem):
        """Show detailed view of an archive item."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        scroll_pos = 0

        # Fetch summary if not already present
        if not item.summary:
            self.status_message = "Fetching item details..."
            self.stdscr.refresh()
            summary = self.archive_manager.get_item_summary(item)
        else:
            summary = item.summary

        while True:
            self.stdscr.clear()
            y = 0

            # Draw title
            self.stdscr.attron(curses.color_pair(1))
            title_lines = textwrap.wrap(item.title, width-2)
            for line in title_lines:
                self.stdscr.addstr(y, 1, line)
                y += 1
            self.stdscr.attroff(curses.color_pair(1))

            # Draw metadata
            y += 1
            self.stdscr.addstr(y, 0, "─" * width)
            y += 1

            metadata = [
                f"Author: {item.author}",
                f"Year: {item.year}",
                f"Format: {item.format}",
                f"Size: {item.size}",
                f"Language: {item.language}"
            ]

            self.stdscr.attron(curses.color_pair(2))
            for line in metadata:
                self.stdscr.addstr(y, 2, line)
                y += 1
            self.stdscr.attroff(curses.color_pair(2))

            y += 1
            self.stdscr.addstr(y, 0, "─" * width)
            y += 1

            # Draw summary
            self.stdscr.addstr(y, 1, "Summary:", curses.A_BOLD)
            y += 1

            summary_lines = textwrap.wrap(summary, width-4)
            visible_lines = height - y - 2

            for i, line in enumerate(summary_lines[scroll_pos:scroll_pos + visible_lines]):
                if y + i >= height - 2:
                    break
                self.stdscr.addstr(y + i, 2, line)

            # Draw status bar
            status = " ↑/↓: Scroll | q: Back to Results "
            self.stdscr.attron(curses.A_REVERSE)
            self.stdscr.addstr(height-1, 0, status.center(width-1))
            self.stdscr.attroff(curses.A_REVERSE)

            self.stdscr.refresh()

            # Handle input
            key = self.stdscr.getch()
            if key == ord('q'):
                break
            elif key in [ord('j'), curses.KEY_DOWN]:
                if scroll_pos + visible_lines < len(summary_lines):
                    scroll_pos += 1
            elif key in [ord('k'), curses.KEY_UP]:
                if scroll_pos > 0:
                    scroll_pos -= 1

@dataclass
class ArxivPaper:
    """Represents an arXiv paper with essential metadata."""
    title: str
    authors: List[str]
    summary: str
    pdf_url: str
    published: datetime
    categories: List[str]
    arxiv_id: str
    primary_category: str
    is_favorite: bool = False
    is_read: bool = False
    local_pdf: Optional[str] = None

class DownloadProgress:
    """Handles download progress tracking and display."""
    def __init__(self, total_size, stdscr, start_y):
        self.total_size = total_size
        self.current_size = 0
        self.start_time = time.time()
        self.stdscr = stdscr
        self.start_y = start_y
        self.last_update = 0
        self.update_interval = 0.1  # Update every 100ms

    def update(self, chunk_size):
        """Update progress with new chunk."""
        self.current_size += chunk_size
        current_time = time.time()

        # Only update display if enough time has passed
        if current_time - self.last_update >= self.update_interval:
            self.display_progress()
            self.last_update = current_time

    def display_progress(self):
        """Display progress bar and statistics."""
        try:
            elapsed_time = time.time() - self.start_time
            percentage = (self.current_size / self.total_size) * 100
            speed = self.current_size / (elapsed_time if elapsed_time > 0 else 1)

            # Calculate time remaining
            if speed > 0:
                remaining_bytes = self.total_size - self.current_size
                eta_seconds = remaining_bytes / speed
                eta_str = self.format_time(eta_seconds)
            else:
                eta_str = "Unknown"

            # Format speed
            speed_str = self.format_size(speed) + "/s"

            # Create progress bar
            width = 50
            filled = int(width * self.current_size // self.total_size)
            bar = "█" * filled + "░" * (width - filled)

            # Format sizes
            current_size_str = self.format_size(self.current_size)
            total_size_str = self.format_size(self.total_size)

            # Display progress information
            self.stdscr.addstr(self.start_y, 0, f"Progress: [{bar}] {percentage:.1f}%")
            self.stdscr.addstr(self.start_y + 1, 0,
                f"Speed: {speed_str:<15} Size: {current_size_str}/{total_size_str}")
            self.stdscr.addstr(self.start_y + 2, 0,
                f"Time remaining: {eta_str:<20}")
            self.stdscr.addstr(self.start_y + 3, 0,
                "Press 'q' to cancel download")

            self.stdscr.refresh()

        except curses.error:
            pass  # Ignore curses errors from terminal resizing

    @staticmethod
    def format_size(size):
        """Format byte size to human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    @staticmethod
    def format_time(seconds):
        """Format seconds to human readable time."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.0f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"


class ArxivManager:
    """Manages arXiv paper fetching and caching."""
    
    # arXiv categories with human-readable names
    CATEGORIES = {
        'cs.AI': 'Artificial Intelligence',
        'cs.CL': 'Computation and Language',
        'cs.CV': 'Computer Vision',
        'cs.LG': 'Machine Learning',
        'cs.NE': 'Neural and Evolutionary Computing',
        'cs.RO': 'Robotics',
        'cs.SE': 'Software Engineering',
        'math.ST': 'Statistics',
        'physics.comp-ph': 'Computational Physics',
        'q-bio.QM': 'Quantitative Methods',
        'stat.ML': 'Machine Learning (Stats)',
        # Add more categories as needed
    }

    def __init__(self, db_conn):
        self.db_conn = db_conn
        self._init_db()
        self.current_category = None
        self.current_search = None
        self.max_results = 50
        self.sort_by = 'date'  # 'date' or 'relevance'
        self.client = arxiv.Client()  # Create a single client instance

    def _init_db(self):
        """Initialize database tables for arXiv papers."""
        c = self.db_conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS arxiv_papers
                    (id TEXT PRIMARY KEY,
                     title TEXT,
                     authors TEXT,
                     summary TEXT,
                     pdf_url TEXT,
                     published TEXT,
                     categories TEXT,
                     primary_category TEXT,
                     read INTEGER DEFAULT 0,
                     favorite INTEGER DEFAULT 0,
                     local_pdf TEXT)''')
        self.db_conn.commit()

    def fetch_papers(self, category: Optional[str] = None, search_query: Optional[str] = None) -> List[ArxivPaper]:
        """Fetch papers from arXiv based on category and/or search query."""
        query_parts = []
        
        if category and category in self.CATEGORIES:
            query_parts.append(f'cat:{category}')
            self.current_category = category
        else:
            self.current_category = None

        if search_query:
            # Clean and format search query
            search_query = search_query.replace(':', ' ').strip()
            if search_query:
                query_parts.append(f'all:{search_query}')
            self.current_search = search_query
        else:
            self.current_search = None

        query = ' AND '.join(query_parts) if query_parts else 'all:*'
        
        # Create client and search
        client = arxiv.Client()
        search = arxiv.Search(
            query=query,
            max_results=self.max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate if self.sort_by == 'date' else arxiv.SortCriterion.Relevance
        )

        papers = []
        try:
            # Use the new client.results() method
            for result in client.results(search):
                paper = ArxivPaper(
                    title=result.title,
                    authors=[str(author) for author in result.authors],
                    summary=result.summary,
                    pdf_url=result.pdf_url,
                    published=result.published,
                    categories=result.categories,
                    arxiv_id=result.entry_id.split('/')[-1],
                    primary_category=result.primary_category
                )
                papers.append(paper)
                self._save_paper(paper)
        except Exception as e:
            print(f"Error fetching papers: {e}")

        return papers

    def _save_paper(self, paper: ArxivPaper):
        """Save paper to database."""
        c = self.db_conn.cursor()
        c.execute('''INSERT OR REPLACE INTO arxiv_papers
                    (id, title, authors, summary, pdf_url, published, categories,
                     primary_category, read, favorite, local_pdf)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (paper.arxiv_id, paper.title, ','.join(paper.authors),
                  paper.summary, paper.pdf_url, paper.published.isoformat(),
                  ','.join(paper.categories), paper.primary_category,
                  paper.is_read, paper.is_favorite, paper.local_pdf))
        self.db_conn.commit()

    def get_saved_papers(self) -> List[ArxivPaper]:
        """Retrieve saved papers from database."""
        c = self.db_conn.cursor()
        c.execute('SELECT * FROM arxiv_papers ORDER BY published DESC')
        papers = []
        for row in c.fetchall():
            paper = ArxivPaper(
                title=row[1],
                authors=row[2].split(','),
                summary=row[3],
                pdf_url=row[4],
                published=datetime.fromisoformat(row[5]),
                categories=row[6].split(','),
                arxiv_id=row[0],
                primary_category=row[7],
                is_read=bool(row[8]),
                is_favorite=bool(row[9]),
                local_pdf=row[10]
            )
            papers.append(paper)
        return papers

    def toggle_favorite(self, paper_id: str):
        """Toggle favorite status of a paper."""
        c = self.db_conn.cursor()
        c.execute('UPDATE arxiv_papers SET favorite = NOT favorite WHERE id = ?', (paper_id,))
        self.db_conn.commit()

    def mark_as_read(self, paper_id: str):
        """Mark a paper as read."""
        c = self.db_conn.cursor()
        c.execute('UPDATE arxiv_papers SET read = 1 WHERE id = ?', (paper_id,))
        self.db_conn.commit()

@dataclass
class ArxivPaper:
    """Represents an arXiv paper with essential metadata."""
    title: str
    authors: List[str]
    summary: str
    pdf_url: str
    published: datetime
    categories: List[str]
    arxiv_id: str
    primary_category: str
    is_favorite: bool = False
    is_read: bool = False
    local_pdf: Optional[str] = None

class ArxivViewer:
    """Enhanced terminal UI for browsing arXiv papers with category navigation."""
    
    def __init__(self, stdscr, arxiv_manager: ArxivManager, colors: ColorScheme):
        self.stdscr = stdscr
        self.arxiv_manager = arxiv_manager
        self.colors = colors
        self.selected_category_index = 0
        self.selected_paper_index = 0
        self.category_scroll_offset = 0
        self.paper_scroll_offset = 0
        self.categories = list(self.arxiv_manager.CATEGORIES.items())
        self.category_papers = {}  # Store papers for each category
        self.active_view = 'categories'  # 'categories' or 'papers'
        self.search_query = ""
        self.status_message = ""
        self.current_sort = 'date'  # 'date' or 'relevance'
        self.paper_cache = {}  # Cache for paper details
        self.favorite_papers = set()  # Store favorite paper IDs
        self.initialize_colors()

    def initialize_colors(self):
        """Initialize color pairs for different categories."""
        for i, _ in enumerate(self.categories, start=10):
            curses.init_pair(i, curses.COLOR_WHITE, -1)

    def fetch_papers_for_category(self, category_id):
        """Fetch papers for a specific category with caching and progress updates."""
        try:
            if category_id not in self.category_papers:
                # Don't call draw functions here to avoid recursion
                papers = []
                
                # Create search object
                client = arxiv.Client()
                search = arxiv.Search(
                    query=f'cat:{category_id}',
                    max_results=self.arxiv_manager.max_results,
                    sort_by=arxiv.SortCriterion.SubmittedDate if self.current_sort == 'date' else arxiv.SortCriterion.Relevance
                )

                # Track progress
                count = 0
                for result in client.results(search):
                    count += 1
                    # Update status message only
                    self._update_status(f"Loading papers for {category_id}... ({count} loaded)")
                    
                    paper = ArxivPaper(
                        title=result.title,
                        authors=[str(author) for author in result.authors],
                        summary=result.summary,
                        pdf_url=result.pdf_url,
                        published=result.published,
                        categories=result.categories,
                        arxiv_id=result.entry_id.split('/')[-1],
                        primary_category=result.primary_category
                    )
                    papers.append(paper)
                    self.arxiv_manager._save_paper(paper)

                # Store papers in cache
                self.category_papers[category_id] = papers
                self._update_status(f"Found {len(papers)} papers in {category_id}")
                
            return self.category_papers[category_id]

        except Exception as e:
            self._update_status(f"Error fetching papers: {str(e)}")
            return []

    def _update_status(self, message):
        """Update status message and refresh only status bar."""
        try:
            self.status_message = message
            height, width = self.stdscr.getmaxyx()
            
            # Count total papers across all categories
            total_papers = sum(len(papers) for papers in self.category_papers.values())
            
            # Update only the status bar line
            status = f"{self.status_message} | Papers: {total_papers} | Press ? for help"
            self.stdscr.move(height-1, 0)
            self.stdscr.clrtoeol()  # Clear the line first
            self.stdscr.attron(curses.A_REVERSE)
            self.stdscr.addstr(height-1, 0, status[:width-1].ljust(width-1))
            self.stdscr.attroff(curses.A_REVERSE)
            self.stdscr.refresh()
        except curses.error:
            pass  # Ignore curses errors during status update

    def draw_papers(self, start_y, height, width):
        """Draw papers for the selected category with enhanced formatting."""
        try:
            category_id, category_name = self.categories[self.selected_category_index]
            
            # Draw category header
            self.stdscr.attron(curses.color_pair(1))
            header = f"═══ {category_name} ({category_id}) ═══"
            self.stdscr.addstr(start_y, (width - len(header)) // 2, header)
            self.stdscr.attroff(curses.color_pair(1))
            start_y += 2

            # Get papers without triggering a screen redraw
            papers = self.category_papers.get(category_id, [])
            if not papers:
                # Show loading message
                self.stdscr.addstr(start_y + 1, 4, "Loading papers...")
                return

            # Draw papers list
            visible_height = height - start_y - 1
            for i, paper in enumerate(papers[self.paper_scroll_offset:]):
                if start_y + i * 3 >= height - 1:
                    break

                is_selected = i + self.paper_scroll_offset == self.selected_paper_index
                y_pos = start_y + i * 3

                if is_selected:
                    self.stdscr.attron(curses.color_pair(3))

                # Draw paper title
                title_prefix = "★ " if paper.arxiv_id in self.favorite_papers else "  "
                title = f"{title_prefix}{paper.title}"
                try:
                    self.stdscr.addstr(y_pos, 4, title[:width-6])
                except curses.error:
                    pass

                # Draw paper metadata
                if not is_selected:
                    self.stdscr.attroff(curses.color_pair(3))
                    self.stdscr.attron(curses.color_pair(2))

                meta = f"  Authors: {', '.join(paper.authors[:2])}{'...' if len(paper.authors) > 2 else ''}"
                try:
                    self.stdscr.addstr(y_pos + 1, 4, meta[:width-6])
                except curses.error:
                    pass

                if is_selected:
                    self.stdscr.attroff(curses.color_pair(3))
                else:
                    self.stdscr.attroff(curses.color_pair(2))

                # Add separator
                if y_pos + 2 < height - 1:
                    try:
                        self.stdscr.addstr(y_pos + 2, 4, "─" * (width-8))
                    except curses.error:
                        pass

            # Draw scrollbar if needed
            if len(papers) > visible_height // 3:
                self.draw_scrollbar(start_y, visible_height, len(papers) * 3, 
                                self.paper_scroll_offset * 3, width-1)
        
        except Exception as e:
            self._update_status(f"Error displaying papers: {str(e)}")

    def handle_input(self):
        """Handle user input with enhanced navigation."""
        key = self.stdscr.getch()

        if key == ord('q'):
            return False

        elif key == ord('J'):  # SHIFT+J - Next category
            if self.active_view == 'categories':
                self.selected_category_index = min(self.selected_category_index + 1, 
                                                len(self.categories) - 1)
                self.adjust_category_scroll()
                # Pre-fetch papers for the new category
                category_id = self.categories[self.selected_category_index][0]
                if category_id not in self.category_papers:
                    self.fetch_papers_for_category(category_id)
            else:
                self.active_view = 'categories'

        elif key == ord('K'):  # SHIFT+K - Previous category
            if self.active_view == 'categories':
                self.selected_category_index = max(0, self.selected_category_index - 1)
                self.adjust_category_scroll()
                # Pre-fetch papers for the new category
                category_id = self.categories[self.selected_category_index][0]
                if category_id not in self.category_papers:
                    self.fetch_papers_for_category(category_id)
            else:
                self.active_view = 'categories'

        elif key in [ord('j'), curses.KEY_DOWN]:  # Next item
            if self.active_view == 'categories':
                self.selected_category_index = min(self.selected_category_index + 1, 
                                                len(self.categories) - 1)
                self.adjust_category_scroll()
            else:
                category_id = self.categories[self.selected_category_index][0]
                papers = self.category_papers.get(category_id, [])
                if papers:
                    self.selected_paper_index = min(self.selected_paper_index + 1, 
                                                len(papers) - 1)
                    self.adjust_paper_scroll()

        elif key in [ord('k'), curses.KEY_UP]:  # Previous item
            if self.active_view == 'categories':
                self.selected_category_index = max(0, self.selected_category_index - 1)
                self.adjust_category_scroll()
            else:
                self.selected_paper_index = max(0, self.selected_paper_index - 1)
                self.adjust_paper_scroll()

        elif key in [ord('\n'), curses.KEY_ENTER, 10]:  # Enter
            if self.active_view == 'categories':
                self.active_view = 'papers'
                self.selected_paper_index = 0
                self.paper_scroll_offset = 0
                # Load papers for selected category
                category_id = self.categories[self.selected_category_index][0]
                if category_id not in self.category_papers:
                    self.fetch_papers_for_category(category_id)
            else:
                self.show_paper_details()

        elif key == ord('b'):  # Back to categories
            self.active_view = 'categories'

        elif key == ord('/'):  # Search
            self.search_papers()

        elif key == ord('s'):  # Toggle sort
            self.current_sort = 'relevance' if self.current_sort == 'date' else 'date'
            self.category_papers.clear()  # Clear cache to refresh with new sort
            self._update_status(f"Sorted by {self.current_sort}")

        elif key == ord('f'):  # Toggle favorite
            if self.active_view == 'papers':
                category_id = self.categories[self.selected_category_index][0]
                papers = self.category_papers.get(category_id, [])
                if papers:
                    paper = papers[self.selected_paper_index]
                    if paper.arxiv_id in self.favorite_papers:
                        self.favorite_papers.remove(paper.arxiv_id)
                        self._update_status(f"Removed from favorites: {paper.title}")
                    else:
                        self.favorite_papers.add(paper.arxiv_id)
                        self._update_status(f"Added to favorites: {paper.title}")

        elif key == ord('o'):  # Open in browser
            if self.active_view == 'papers':
                category_id = self.categories[self.selected_category_index][0]
                papers = self.category_papers.get(category_id, [])
                if papers:
                    paper = papers[self.selected_paper_index]
                    webbrowser.open(paper.pdf_url)
                    self._update_status(f"Opened in browser: {paper.title}")

        elif key == ord('p'):  # Generate PDF summary
            if self.active_view == 'papers':
                category_id = self.categories[self.selected_category_index][0]
                papers = self.category_papers.get(category_id, [])
                if papers:
                    self.generate_paper_summary(papers[self.selected_paper_index])

        elif key == ord('h') or key == ord('?'):  # Show help
            self.show_help()

        return True

    def draw_screen(self):
        """Draw the main interface with categories and papers."""
        try:
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()

            # Draw header with current mode and search info
            header = f"═══ arXiv Browser ═══ [{self.current_sort}]"
            if self.search_query:
                header += f" | Search: {self.search_query}"
            self.stdscr.attron(curses.color_pair(1))
            self.stdscr.addstr(0, (width - len(header)) // 2, header)
            self.stdscr.attroff(curses.color_pair(1))
            self.stdscr.addstr(1, 0, "─" * width)

            if self.active_view == 'categories':
                self.draw_categories(2, height, width)
            else:
                self.draw_papers(2, height, width)

            # Draw status bar
            try:
                # Count total papers across all categories
                total_papers = sum(len(papers) for papers in self.category_papers.values())
                status = f"{self.status_message} | Papers: {total_papers} | Press ? for help"
                self.stdscr.attron(curses.A_REVERSE)
                self.stdscr.addstr(height-1, 0, status[:width-1].ljust(width-1))
                self.stdscr.attroff(curses.A_REVERSE)
            except curses.error:
                pass

            self.stdscr.refresh()
        except Exception as e:
            # If drawing fails, at least try to show an error
            try:
                self.stdscr.clear()
                self.stdscr.addstr(0, 0, f"Display error: {str(e)}")
                self.stdscr.refresh()
            except curses.error:
                pass

    def search_papers(self):
        """Search papers with query input."""
        height = self.stdscr.getmaxyx()[0]
        
        # Show search prompt
        curses.echo()
        curses.curs_set(1)
        self.stdscr.addstr(height-1, 0, "Search: ")
        self.stdscr.refresh()
        
        query = self.stdscr.getstr().decode('utf-8').strip()
        
        curses.noecho()
        curses.curs_set(0)
        
        if query:
            self.search_query = query
            self.status_message = "Searching..."
            self.stdscr.refresh()
            
            # Clear existing papers and fetch new ones
            self.category_papers.clear()
            papers = self.arxiv_manager.fetch_papers(search_query=query)
            
            if papers:
                # Store papers in a special search category
                self.category_papers['search_results'] = papers
                self.selected_category_index = 0
                self.selected_paper_index = 0
                self.paper_scroll_offset = 0
                self.active_view = 'papers'
                self.status_message = f"Found {len(papers)} papers"
            else:
                self.status_message = "No papers found"

    def show_help(self):
        """Show help screen with keyboard shortcuts."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        
        help_text = [
            "Keyboard Shortcuts",
            "═════════════════",
            "",
            "Navigation:",
            "  j/↓, k/↑      : Navigate items",
            "  J/K (Shift)   : Move between categories",
            "  Enter         : View selected item",
            "  b             : Back to categories",
            "",
            "Actions:",
            "  o             : Open PDF in browser",
            "  p             : Generate PDF summary",
            "  f             : Toggle favorite",
            "  /             : Search papers",
            "  s             : Toggle sort (date/relevance)",
            "",
            "View Controls:",
            "  q             : Quit current view",
            "  h/?           : Show this help",
            "",
            "Press any key to close help"
        ]
        
        # Center help text
        start_y = (height - len(help_text)) // 2
        for i, line in enumerate(help_text):
            if start_y + i >= height - 1:
                break
            x = (width - len(line)) // 2
            try:
                if i <= 1:  # Header
                    self.stdscr.attron(curses.color_pair(1))
                    self.stdscr.addstr(start_y + i, x, line)
                    self.stdscr.attroff(curses.color_pair(1))
                elif line and line[0] not in ' ':  # Section headers
                    self.stdscr.attron(curses.A_BOLD)
                    self.stdscr.addstr(start_y + i, x, line)
                    self.stdscr.attroff(curses.A_BOLD)
                else:  # Normal text
                    self.stdscr.addstr(start_y + i, x, line)
            except curses.error:
                pass
        
        self.stdscr.refresh()
        self.stdscr.getch()

    def generate_paper_summary(self, paper: ArxivPaper):
        """Generate a PDF summary of the paper by downloading and analyzing it."""
        screen_state = None
        try:
            # Save screen state
            screen_state = curses.def_prog_mode()
            
            # Temporarily exit curses mode
            curses.endwin()
            
            print(f"\nProcessing paper: {paper.title}")
            print("Downloading PDF... This may take a moment.")
            
            # Create a temporary directory for our files
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    # Download the PDF
                    pdf_path = os.path.join(temp_dir, f"{paper.arxiv_id}.pdf")
                    try:
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                        }
                        response = requests.get(paper.pdf_url, headers=headers, stream=True)
                        response.raise_for_status()
                        
                        # Show download progress
                        total_size = int(response.headers.get('content-length', 0))
                        block_size = 8192
                        downloaded = 0
                        
                        with open(pdf_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=block_size):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if total_size:
                                        percent = (downloaded / total_size) * 100
                                        print(f"\rDownload progress: {percent:.1f}%", end='')
                        
                        print("\nPDF downloaded successfully.")
                        print("Analyzing paper...")
                        
                        # Format metadata first
                        summary_text = f"""Paper Analysis: {paper.title}

    METADATA
    ════════
    Authors: {', '.join(paper.authors)}
    Published: {paper.published.strftime('%Y-%m-%d %H:%M:%S')}
    Categories: {', '.join(paper.categories)}
    Primary Category: {paper.primary_category}
    arXiv ID: {paper.arxiv_id}
    PDF URL: {paper.pdf_url}

    ABSTRACT
    ════════
    {paper.summary}

    DETAILED ANALYSIS
    ════════════════
    """
                        
                        # Create analysis prompt
                        analysis_prompt = f"""Please analyze this academic paper and provide a comprehensive summary that includes:
    1. Main objectives and research goals
    2. Key methodologies used
    3. Main findings and results
    4. Significant conclusions
    5. Technical innovations or contributions

    Start your analysis directly with the content - no need for headers or metadata."""
                        
                        try:
                            # Run analysis and generate final PDF in one step
                            summary_process = subprocess.run(
                                ['/usr/local/bin/wpdf', '-m', 'gemini-2.0-flash-exp', '-a', pdf_path, 
                                summary_text + analysis_prompt],
                                check=True
                            )
                            
                            print("\nPDF summary generated successfully!")
                            
                        except subprocess.CalledProcessError as e:
                            print(f"\nError generating PDF summary: {e}")
                            if e.stdout:
                                print("stdout:", e.stdout)
                            if e.stderr:
                                print("stderr:", e.stderr)
                        
                    except requests.RequestException as e:
                        print(f"\nError downloading PDF: {e}")
                    except Exception as e:
                        print(f"\nUnexpected error during download: {str(e)}")
                
                except Exception as e:
                    print(f"\nError processing paper: {str(e)}")
                
                print("\nPress Enter to continue...")
                input()
                
        except Exception as e:
            print(f"\nUnexpected error: {str(e)}")
            print("\nPress Enter to continue...")
            input()
                
        finally:
            # Restore terminal state
            try:
                if screen_state is not None:
                    curses.reset_prog_mode()
                    self.stdscr.refresh()
                else:
                    # Fallback restoration if screen state wasn't saved
                    self.stdscr = curses.initscr()
                    curses.noecho()
                    curses.cbreak()
                    self.stdscr.keypad(1)
                    curses.start_color()
                    self.colors.setup_colors()
                    curses.curs_set(0)
            except Exception as e:
                print(f"\nError restoring terminal: {str(e)}")
                print("\nPress Enter to continue...")
                input()
            
            # Update status message
            self._update_status("PDF summary complete")        

    def run(self):
        """Main run loop with improved error handling."""
        try:
            # Initialize curses
            curses.start_color()
            curses.use_default_colors()
            curses.noecho()
            curses.cbreak()
            self.stdscr.keypad(1)
            curses.curs_set(0)
            
            while True:
                try:
                    self.draw_screen()
                    if not self.handle_input():
                        break
                except curses.error:
                    # Handle terminal resize
                    self.stdscr.refresh()
        except Exception as e:
            self.status_message = f"Error: {str(e)}"
            self.stdscr.refresh()
            time.sleep(2)
        finally:
            # Cleanup
            try:
                # Reset terminal settings
                curses.nocbreak()
                curses.echo()
                self.stdscr.keypad(0)
                # Clear screen before exiting to prevent visual artifacts
                self.stdscr.clear()
                self.stdscr.refresh()
            except:
                pass
            # Final endwin
            try:
                curses.endwin()
            except:
                pass

    def draw_categories(self, start_y, height, width):
        """Draw category list with visual improvements."""
        visible_height = height - start_y - 1
        
        for i, (cat_id, cat_name) in enumerate(self.categories[self.category_scroll_offset:]):
            if start_y + i >= height - 1:
                break

            # Calculate paper count if category has been loaded
            paper_count = len(self.category_papers.get(cat_id, []))
            paper_info = f" [{paper_count} papers]" if paper_count > 0 else ""

            # Format category line
            is_selected = i + self.category_scroll_offset == self.selected_category_index
            prefix = "▼ " if is_selected else "  "
            
            # Apply appropriate color and style
            if is_selected:
                self.stdscr.attron(curses.color_pair(3))
            else:
                self.stdscr.attron(curses.color_pair((i % 6) + 10))

            # Construct and display the category line
            category_text = f"{prefix}{cat_name} ({cat_id}){paper_info}"
            try:
                self.stdscr.addstr(start_y + i, 2, category_text[:width-4])
            except curses.error:
                pass

            # Reset attributes
            if is_selected:
                self.stdscr.attroff(curses.color_pair(3))
            else:
                self.stdscr.attroff(curses.color_pair((i % 6) + 10))

        # Draw scrollbar if needed
        if len(self.categories) > visible_height:
            self.draw_scrollbar(start_y, visible_height, len(self.categories), 
                              self.category_scroll_offset, width-1)

    
    def draw_scrollbar(self, start_y, visible_height, total_items, scroll_offset, x_pos):
        """Draw a visual scrollbar."""
        if total_items <= visible_height:
            return

        # Calculate scrollbar dimensions
        bar_height = max(1, int(visible_height * visible_height / total_items))
        bar_pos = int(scroll_offset * (visible_height - bar_height) / (total_items - visible_height))

        # Draw scrollbar
        for i in range(visible_height):
            try:
                if bar_pos <= i < bar_pos + bar_height:
                    self.stdscr.addstr(start_y + i, x_pos, "█")
                else:
                    self.stdscr.addstr(start_y + i, x_pos, "│")
            except curses.error:
                pass

    def handle_input(self):
        """Handle user input with enhanced navigation."""
        key = self.stdscr.getch()

        if key == ord('q'):
            return False

        elif key == ord('J'):  # SHIFT+J - Next category
            if self.active_view == 'categories':
                self.selected_category_index = min(self.selected_category_index + 1, 
                                                 len(self.categories) - 1)
                self.adjust_category_scroll()
            else:
                self.active_view = 'categories'

        elif key == ord('K'):  # SHIFT+K - Previous category
            if self.active_view == 'categories':
                self.selected_category_index = max(0, self.selected_category_index - 1)
                self.adjust_category_scroll()
            else:
                self.active_view = 'categories'

        elif key in [ord('j'), curses.KEY_DOWN]:  # Next item
            if self.active_view == 'categories':
                self.selected_category_index = min(self.selected_category_index + 1, 
                                                 len(self.categories) - 1)
                self.adjust_category_scroll()
            else:
                category_papers = self.fetch_papers_for_category(
                    self.categories[self.selected_category_index][0])
                self.selected_paper_index = min(self.selected_paper_index + 1, 
                                              len(category_papers) - 1)
                self.adjust_paper_scroll()

        elif key in [ord('k'), curses.KEY_UP]:  # Previous item
            if self.active_view == 'categories':
                self.selected_category_index = max(0, self.selected_category_index - 1)
                self.adjust_category_scroll()
            else:
                self.selected_paper_index = max(0, self.selected_paper_index - 1)
                self.adjust_paper_scroll()

        elif key in [ord('\n'), curses.KEY_ENTER, 10]:  # Enter
            if self.active_view == 'categories':
                self.active_view = 'papers'
                self.selected_paper_index = 0
                self.paper_scroll_offset = 0
            else:
                self.show_paper_details()

        elif key == ord('b'):  # Back to categories
            self.active_view = 'categories'

        elif key == ord('/'):  # Search
            self.search_papers()

        elif key == ord('s'):  # Toggle sort
            self.current_sort = 'relevance' if self.current_sort == 'date' else 'date'
            self.category_papers.clear()  # Clear cache to refresh with new sort
            self.status_message = f"Sorted by {self.current_sort}"

        elif key == ord('f'):  # Toggle favorite
            if self.active_view == 'papers':
                category_papers = self.fetch_papers_for_category(
                    self.categories[self.selected_category_index][0])
                if category_papers:
                    paper = category_papers[self.selected_paper_index]
                    if paper.arxiv_id in self.favorite_papers:
                        self.favorite_papers.remove(paper.arxiv_id)
                        self.status_message = f"Removed from favorites: {paper.title}"
                    else:
                        self.favorite_papers.add(paper.arxiv_id)
                        self.status_message = f"Added to favorites: {paper.title}"

        elif key == ord('o'):  # Open in browser
            if self.active_view == 'papers':
                category_papers = self.fetch_papers_for_category(
                    self.categories[self.selected_category_index][0])
                if category_papers:
                    paper = category_papers[self.selected_paper_index]
                    webbrowser.open(paper.pdf_url)
                    self.status_message = f"Opened in browser: {paper.title}"

        elif key == ord('p'):  # Generate PDF summary
            if self.active_view == 'papers':
                category_papers = self.fetch_papers_for_category(
                    self.categories[self.selected_category_index][0])
                if category_papers:
                    self.generate_paper_summary(category_papers[self.selected_paper_index])

        elif key == ord('h') or key == ord('?'):  # Show help
            self.show_help()

        return True

    def adjust_category_scroll(self):
        """Adjust category scroll position to keep selected category visible."""
        height = self.stdscr.getmaxyx()[0]
        visible_categories = height - 4  # Adjust for header and status bar

        if self.selected_category_index < self.category_scroll_offset:
            self.category_scroll_offset = self.selected_category_index
        elif self.selected_category_index >= self.category_scroll_offset + visible_categories:
            self.category_scroll_offset = self.selected_category_index - visible_categories + 1

    def adjust_paper_scroll(self):
        """Adjust paper scroll position to keep selected paper visible."""
        height = self.stdscr.getmaxyx()[0]
        visible_papers = (height - 6) // 3  # Adjust for header, category header, and status bar

        if self.selected_paper_index < self.paper_scroll_offset:
            self.paper_scroll_offset = self.selected_paper_index
        elif self.selected_paper_index >= self.paper_scroll_offset + visible_papers:
            self.paper_scroll_offset = self.selected_paper_index - visible_papers + 1

    def show_paper_details(self):
        """Show detailed view of the selected paper with improved formatting."""
        if self.active_view != 'papers':
            return

        category_papers = self.fetch_papers_for_category(
            self.categories[self.selected_category_index][0])
        if not category_papers:
            return

        paper = category_papers[self.selected_paper_index]
        
        self.stdscr.clear()
        height, width = self.stdscr.get

class Article:
    def __init__(self, title: str, url: str):
        self.title = title
        self.url = url
        self.source = urllib.parse.urlparse(url).netloc
        self.tags: Set[str] = set()
        self.is_favorite = False
        self.is_read = False
        self.summary = ""
        self.content = ""
        self.published = ""
        
    def to_dict(self) -> dict:
        return {
            'title': self.title,
            'url': self.url,
            'source': self.source,
            'tags': list(self.tags),
            'is_favorite': self.is_favorite,
            'is_read': self.is_read,
            'summary': self.summary,
            'content': self.content,
            'published': self.published
        }
        
    @classmethod
    def from_dict(cls, data: dict) -> 'Article':
        article = cls(data['title'], data['url'])
        article.tags = set(data.get('tags', []))
        article.is_favorite = data.get('is_favorite', False)
        article.is_read = data.get('is_read', False)
        article.summary = data.get('summary', "")
        article.content = data.get('content', "")
        article.published = data.get('published', "")
        return article

class ArticleViewer:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.articles: List[Article] = []
        self.filtered_articles: List[Article] = []
        self.history: Set[int] = set()
        self.bookmarks: Set[int] = set()
        self.selected_index = 0
        self.top_index = 0
        self.filter_mode = None
        self.filter_text = ""
        self.status_message = ""
        self.sources: Dict[str, int] = defaultdict(int)
        self.tags: Set[str] = set()
        self.colors = ColorScheme()
        self.article_view_mode = False
        self.feed_manager = FeedManager()
        self.view_mode = 'brutalist'  # 'brutalist' or 'rss'
        self.feed_manager.start_auto_update()
        self.load_data()

    def cleanup(self):
        """Enhanced cleanup to ensure proper terminal restoration."""
        try:
            # Stop any background processes
            self.feed_manager.stop_auto_update()
            
            # Restore terminal state
            try:
                self.stdscr.keypad(0)
                curses.nocbreak()
                curses.echo()
                curses.curs_set(1)
            except:
                pass
                
            # Clear screen before exiting
            try:
                self.stdscr.clear()
                self.stdscr.refresh()
            except:
                pass
                
        except Exception as e:
            print(f"\nCleanup error: {e}")
            
    def cycle_view(self, direction='forward'):
        """Enhanced view cycling with proper cleanup."""
        try:
            # Perform cleanup before switching views
            self.cleanup()
            
            # Cycle through view modes
            views = ['brutalist', 'rss', 'annas', 'arxiv']
            current_index = views.index(self.view_mode)
            
            if direction == 'forward':
                next_index = (current_index + 1) % len(views)
            else:  # backward
                next_index = (current_index - 1) % len(views)
                
            self.view_mode = views[next_index]
            
            # Reinitialize for new view
            if self.view_mode != 'annas':
                self.load_data()
                
            # Reinitialize curses for new view
            curses.start_color()
            curses.use_default_colors()
            curses.noecho()
            curses.cbreak()
            self.stdscr.keypad(1)
            curses.curs_set(0)
            self.colors.setup_colors()
            
            self.status_message = f"Switched to {self.view_mode.upper()} view"
            
        except Exception as e:
            self.status_message = f"Error switching views: {e}"

    def load_data(self):
        """Load articles based on current view mode."""
        if self.view_mode == 'brutalist':
            self.articles = self.load_articles()
        elif self.view_mode == 'rss':
            self.articles = self.load_rss_articles()
        # Anna's Archive mode doesn't need preloaded data
            
        if self.view_mode in ['brutalist', 'rss']:
            self.filtered_articles = self.articles.copy()
            self.history = set(load_history())
            self.bookmarks = self.load_bookmarks()
            
            # Update sources count
            self.sources.clear()
            for article in self.articles:
                self.sources[article.source] += 1
                self.tags.update(article.tags)

    def show_feed_stats(self):
        """Show detailed statistics for RSS feeds."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        header = "═══ Feed Statistics ═══"
        self.stdscr.attron(curses.color_pair(1))
        self.stdscr.addstr(0, (width - len(header)) // 2, header)
        self.stdscr.attroff(curses.color_pair(1))

        y = 2
        stats = []

        c = self.feed_manager.db_conn.cursor()
        for name, url in self.feed_manager.feeds.items():
            c.execute('''SELECT COUNT(*),
                               SUM(CASE WHEN read = 1 THEN 1 ELSE 0 END),
                               SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END)
                        FROM articles WHERE feed_url = ?''', (url,))
            total, read, fav = c.fetchone()

            if total:
                read_percent = (read or 0) * 100 / total
                stats.append({
                    'name': name,
                    'total': total or 0,
                    'read': read or 0,
                    'read_percent': read_percent,
                    'favorites': fav or 0
                })

        if stats:
            # Header
            self.stdscr.addstr(y, 2, "Feed Name")
            self.stdscr.addstr(y, 30, "Articles")
            self.stdscr.addstr(y, 40, "Read")
            self.stdscr.addstr(y, 50, "Read %")
            self.stdscr.addstr(y, 60, "Favorites")
            y += 1
            self.stdscr.addstr(y, 0, "─" * (width-1))
            y += 1

            # Data
            for stat in sorted(stats, key=lambda x: x['total'], reverse=True):
                if y >= height - 3:
                    break

                self.stdscr.addstr(y, 2, stat['name'][:25].ljust(25))
                self.stdscr.addstr(y, 30, str(stat['total']).rjust(8))
                self.stdscr.addstr(y, 40, str(stat['read']).rjust(8))
                self.stdscr.addstr(y, 50, f"{stat['read_percent']:.1f}%".rjust(8))
                self.stdscr.addstr(y, 60, str(stat['favorites']).rjust(8))
                y += 1
        else:
            self.stdscr.addstr(y, 2, "No feed statistics available yet")

        # Footer
        self.stdscr.addstr(height-2, 0, "Press any key to return")
        self.stdscr.refresh()
        self.stdscr.getch()

    def view_feed_articles(self, feed_name, feed_url):
        """View articles from a specific feed."""
        selected_index = 0
        scroll_offset = 0
        
        while True:
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            
            # Draw header
            header = f"═══ {feed_name} Articles ═══"
            self.stdscr.attron(curses.color_pair(1))
            self.stdscr.addstr(0, (width - len(header)) // 2, header)
            self.stdscr.attroff(curses.color_pair(1))
            self.stdscr.addstr(1, 0, "─" * width)
            
            # Get articles for this feed
            c = self.feed_manager.db_conn.cursor()
            c.execute('''SELECT title, url, published, read, favorite 
                        FROM articles 
                        WHERE feed_url = ? 
                        ORDER BY published DESC''', (feed_url,))
            articles = c.fetchall()
            
            # Calculate visible range
            max_visible = height - 6  # Leave room for header and footer
            visible_articles = articles[scroll_offset:scroll_offset + max_visible]
            
            # Show articles
            for i, (title, url, published, read, favorite) in enumerate(visible_articles):
                y = i + 2
                if y >= height - 2:
                    break
                
                # Format date
                try:
                    date = datetime.fromisoformat(published).strftime("%Y-%m-%d")
                except:
                    date = "Unknown date"
                
                # Create status indicators
                status = ""
                if read:
                    status += "✓"
                if favorite:
                    status += "★"
                
                # Highlight selected article
                if scroll_offset + i == selected_index:
                    self.stdscr.attron(curses.color_pair(3))
                
                # Display article info
                prefix = f"{status:<2}"
                title_space = width - len(prefix) - len(date) - 4
                truncated_title = title[:title_space] + "..." if len(title) > title_space else title
                try:
                    self.stdscr.addstr(y, 0, prefix)
                    self.stdscr.addstr(y, len(prefix), truncated_title)
                    self.stdscr.addstr(y, width - len(date) - 1, date)
                except curses.error:
                    pass
                
                if scroll_offset + i == selected_index:
                    self.stdscr.attroff(curses.color_pair(3))
            
            # Show scroll indicators
            if scroll_offset > 0:
                self.stdscr.addstr(2, width-1, "↑")
            if scroll_offset + max_visible < len(articles):
                self.stdscr.addstr(min(height-3, max_visible+1), width-1, "↓")
            
            # Show controls
            controls = " ENTER: Read | o: Open in browser | p: Generate PDF | m: Mark read/unread | f: Favorite | q: Back "
            try:
                self.stdscr.attron(curses.A_REVERSE)
                self.stdscr.addstr(height-1, 0, controls.center(width))
                self.stdscr.attroff(curses.A_REVERSE)
            except curses.error:
                pass
            
            self.stdscr.refresh()
            
            # Handle input
            key = self.stdscr.getch()
            
            if key == ord('q'):
                break
            elif key in [ord('j'), curses.KEY_DOWN] and articles:
                selected_index = min(selected_index + 1, len(articles) - 1)
                if selected_index >= scroll_offset + max_visible:
                    scroll_offset += 1
            elif key == 72:  # Capital H (Shift+H) - move backward in views
                self.cycle_view('backward')
            elif key == 76:  # Capital L (Shift+L) - move forward in views
                self.cycle_view('forward')
            elif key in [ord('k'), curses.KEY_UP] and articles:
                selected_index = max(selected_index - 1, 0)
                if selected_index < scroll_offset:
                    scroll_offset -= 1
            elif key in [ord('\n'), ord('v')] and articles:  # Enter or 'v' to read
                article = articles[selected_index]
                self.show_article_content_from_db(article[0], article[1], article[2])
                # Mark as read
                c.execute('UPDATE articles SET read = 1 WHERE url = ?', (article[1],))
                self.feed_manager.db_conn.commit()
            elif key == ord('o') and articles:  # Open in browser
                article = articles[selected_index]
                webbrowser.open(article[1])
                c.execute('UPDATE articles SET read = 1 WHERE url = ?', (article[1],))
                self.feed_manager.db_conn.commit()
            elif key == ord('m') and articles:  # Toggle read status
                article = articles[selected_index]
                c.execute('UPDATE articles SET read = NOT read WHERE url = ?', (article[1],))
                self.feed_manager.db_conn.commit()
            elif key == ord('f') and articles:  # Toggle favorite
                article = articles[selected_index]
                c.execute('UPDATE articles SET favorite = NOT favorite WHERE url = ?', (article[1],))
                self.feed_manager.db_conn.commit()
            elif key == ord('p') and articles:  # Generate PDF
                try:
                    article = articles[selected_index]
                    title, url = article[0], article[1]
                    
                    # Get content from database or fetch it
                    c.execute('SELECT content FROM articles WHERE url = ?', (url,))
                    result = c.fetchone()
                    content = result[0] if result else scrape_article(url)
                    
                    # Temporarily exit curses
                    curses.nocbreak()
                    curses.echo()
                    curses.endwin()
                    
                    # Format content for PDF
                    formatted_text = f"Title: {title}\nURL: {url}\n\nContent:\n{content}"
                    
                    try:
                        print("\nGenerating PDF...")
                        subprocess.run(
                            ['/usr/local/bin/wpdf', formatted_text],
                            capture_output=True,
                            text=True,
                            check=True
                        )
                        print("PDF generated successfully!")
                        # Mark as read
                        c.execute('UPDATE articles SET read = 1 WHERE url = ?', (url,))
                        self.feed_manager.db_conn.commit()
                    except subprocess.CalledProcessError as e:
                        print(f"\nError generating PDF: {e}")
                    
                    print("\nPress Enter to continue...")
                    input()
                    
                finally:
                    # Restore terminal to curses mode
                    self.stdscr = curses.initscr()
                    curses.noecho()
                    curses.cbreak()
                    self.stdscr.keypad(1)
                    curses.start_color()
                    self.colors.setup_colors()
                    curses.curs_set(0)


    def show_article_content_from_db(self, title, url, published):
        """Display article content from database with improved formatting."""
        c = self.feed_manager.db_conn.cursor()
        c.execute('SELECT content FROM articles WHERE url = ?', (url,))
        result = c.fetchone()
        content = result[0] if result else None

        if not content:
            content = scrape_article(url)

        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        lines = []

        # Format title block
        try:
            date = datetime.fromisoformat(published).strftime("%Y-%m-%d %H:%M")
        except:
            date = "Unknown date"

        lines.extend([
            "═" * width,
            "",
            title.center(width),
            "",
            f"Published: {date}".center(width),
            f"URL: {url}".center(width),
            "",
            "═" * width,
            ""
        ])

        # Format content
        if content:
            # Wrap content to terminal width
            import textwrap
            wrapped_content = textwrap.fill(content, width-4)  # -4 for margins
            lines.extend([("  " + line) for line in wrapped_content.split('\n')])

        # Display with scrolling
        scroll_pos = 0
        while True:
            self.stdscr.clear()

            # Show content
            for i in range(height - 1):
                if scroll_pos + i < len(lines):
                    try:
                        self.stdscr.addstr(i, 0, lines[scroll_pos + i][:width-1])
                    except curses.error:
                        pass

            # Show status bar
            status = " ↑/↓: Scroll | q: Return | o: Open in browser "
            try:
                self.stdscr.attron(curses.A_REVERSE)
                self.stdscr.addstr(height-1, 0, status.center(width))
                self.stdscr.attroff(curses.A_REVERSE)
            except curses.error:
                pass

            self.stdscr.refresh()

            key = self.stdscr.getch()
            if key == ord('q'):
                break
            elif key == ord('o'):
                webbrowser.open(url)
            elif key in [ord('j'), curses.KEY_DOWN]:
                scroll_pos = min(scroll_pos + 1, max(0, len(lines) - height + 1))
            elif key in [ord('k'), curses.KEY_UP]:
                scroll_pos = max(0, scroll_pos - 1)
            elif key == ord('b'):  # Page up
                scroll_pos = max(0, scroll_pos - (height - 2))
            elif key == ord('f'):  # Page down
                scroll_pos = min(scroll_pos + (height - 2), max(0, len(lines) - height + 1))

    def load_articles(self) -> List[Article]:
        """Load articles from cache or fetch new ones."""
        cached = load_cached_links()
        if cached:
            return [Article(title, url) for title, url in cached]
        return self.fetch_fresh_articles()

    def fetch_fresh_articles(self) -> List[Article]:
        """Fetch fresh articles from the source."""
        articles = []
        links = extract_links_from_brutalist()
        for title, url in links:
            article = Article(title, url)
            articles.append(article)
        save_links_to_cache([(a.title, a.url) for a in articles])
        return articles

    def load_rss_articles(self) -> List[Article]:
        """Load articles from RSS feeds."""
        articles = []
        c = self.feed_manager.db_conn.cursor()
        c.execute('''SELECT title, url, source, content, published, read, favorite 
                    FROM articles ORDER BY published DESC''')
        
        for row in c.fetchall():
            article = Article(row[0], row[1])
            article.source = row[2]
            article.content = row[3]
            article.published = row[4]
            article.is_read = bool(row[5])
            article.is_favorite = bool(row[6])
            articles.append(article)
            
        return articles

    def load_bookmarks(self) -> Set[int]:
        """Load saved bookmarks."""
        try:
            if BOOKMARKS_FILE.exists():
                with open(BOOKMARKS_FILE) as f:
                    return set(json.load(f)['bookmarks'])
            return set()
        except Exception:
            return set()

    def save_bookmarks(self):
        """Save bookmarks to file."""
        try:
            BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(BOOKMARKS_FILE, 'w') as f:
                json.dump({'bookmarks': list(self.bookmarks)}, f)
        except Exception as e:
            self.status_message = f"Error saving bookmarks: {e}"

    def apply_filter(self):
        """Apply current filter to articles."""
        if not self.filter_text:
            self.filtered_articles = self.articles.copy()
            return

        filtered = []
        search_terms = self.filter_text.lower().split()
        
        for article in self.articles:
            if self.filter_mode == 'source':
                if any(term in article.source.lower() for term in search_terms):
                    filtered.append(article)
            elif self.filter_mode == 'tag':
                if any(term in tag.lower() for term in search_terms for tag in article.tags):
                    filtered.append(article)
            else:  # Default search in title and URL
                if any(term in article.title.lower() or term in article.url.lower() 
                      for term in search_terms):
                    filtered.append(article)
        
        self.filtered_articles = filtered
        self.selected_index = 0
        self.top_index = 0

    def toggle_bookmark(self):
        """Toggle bookmark status of selected article."""
        if self.filtered_articles:
            idx = self.articles.index(self.filtered_articles[self.selected_index])
            if idx in self.bookmarks:
                self.bookmarks.remove(idx)
            else:
                self.bookmarks.add(idx)
            self.save_bookmarks()
            self.status_message = "Bookmark toggled"

    def show_source_stats(self):
        """Display statistics about news sources."""
        self.stdscr.clear()
        self.stdscr.addstr(0, 0, "News Sources Statistics", curses.A_BOLD)
        self.stdscr.addstr(1, 0, "─" * 40)
        
        y = 2
        for source, count in sorted(self.sources.items(), key=lambda x: x[1], reverse=True):
            if y >= curses.LINES - 2:
                break
            self.stdscr.addstr(y, 0, f"{source}: {count} articles")
            y += 1
            
        self.stdscr.addstr(y + 1, 0, "Press any key to continue...")
        self.stdscr.refresh()
        self.stdscr.getch()

    def draw_status_bar(self):
        """Draw the status bar at the bottom of the screen."""
        height, width = self.stdscr.getmaxyx()
        status = f" {self.status_message} | Articles: {len(self.filtered_articles)}/{len(self.articles)} | Press ? for help"
        status = status[:width-1]  # Truncate to fit
        self.stdscr.attron(curses.A_REVERSE)
        try:
            self.stdscr.addstr(height-1, 0, status.ljust(width-1))
        except curses.error:
            pass
        self.stdscr.attroff(curses.A_REVERSE)

    def generate_pdf(self):
        """Generate PDF for current article."""
        if self.filtered_articles:
            article = self.filtered_articles[self.selected_index]
            curses.endwin()  # Temporarily exit curses mode
            success = process_article_pdf(article, OUTPUT_DIR)
            if success:
                save_to_history(self.selected_index)
            # Restore curses mode
            self.stdscr = curses.initscr()
            curses.start_color()
            self.colors.setup_colors()

    def process_article_pdf(article, output_dir):
        """Generate PDF for the article."""
        try:
            # Format content for PDF
            content = scrape_article(article.url)
            formatted_text = f"""
    Title: {article.title}
    URL: {article.url}

    Content:
    {content}
            """
            
            # Create filename from title (sanitize it for filesystem)
            safe_title = "".join(c for c in article.title if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = f"{safe_title[:50]}.pdf"
            filepath = output_dir / filename
            
            # Use wpdf to generate PDF
            process = subprocess.run(
                ['/usr/local/bin/wpdf', formatted_text],
                capture_output=True,
                text=True,
                check=True
            )
            
            if process.returncode == 0:
                print(f"\nPDF generated: {filepath}")
                return True
        except subprocess.CalledProcessError as e:
            print(f"\nError generating PDF: {e}")
        except Exception as e:
            print(f"\nUnexpected error: {e}")
        
        return False

    def draw_articles(self):
            """Draw the main article list view."""
            if self.view_mode == 'annas':
                if self.annas_viewer:
                    self.annas_viewer.draw_search_results()
                return

            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            current_y = 0
            
            # Draw header
            header = f"{self.view_mode.title()} News Reader"
            if self.filter_mode:
                header += f" | Filter: {self.filter_mode} '{self.filter_text}'"
            self.stdscr.attron(curses.color_pair(1))
            self.stdscr.addstr(current_y, 0, header[:width-1])
            self.stdscr.attroff(curses.color_pair(1))
            self.stdscr.addstr(current_y + 1, 0, "─" * (width-1))
            current_y += 2

            if self.view_mode == 'rss':
                # Group articles by feed
                feeds = {}
                for article in self.filtered_articles:
                    if article.source not in feeds:
                        feeds[article.source] = []
                    feeds[article.source].append(article)

                # Display articles grouped by feed
                for feed_name, feed_articles in feeds.items():
                    if current_y >= height - 3:
                        break

                    # Draw feed header
                    self.stdscr.attron(curses.color_pair(1))
                    feed_header = f"═══ {feed_name} ═══"
                    self.stdscr.addstr(current_y, 2, feed_header[:width-4])
                    self.stdscr.attroff(curses.color_pair(1))
                    current_y += 1

                    # Draw articles for this feed
                    for article in feed_articles:
                        if current_y >= height - 3:
                            break

                        # Get article index in the full filtered list
                        article_idx = self.filtered_articles.index(article)
                        
                        # Format article line
                        prefix = ""
                        if self.articles.index(article) in self.history:
                            prefix += "✓"
                        if self.articles.index(article) in self.bookmarks:
                            prefix += "★"
                        prefix = f"{prefix:2}"

                        # Show title
                        if article_idx == self.selected_index:
                            self.stdscr.attron(curses.color_pair(3))
                        else:
                            self.stdscr.attron(curses.color_pair(1))

                        # Add timestamp if available
                        timestamp = ""
                        if article.published:
                            try:
                                dt = datetime.fromisoformat(article.published)
                                timestamp = dt.strftime("%Y-%m-%d %H:%M") + " "
                            except:
                                pass

                        # Format and display the line
                        title_space = width - len(prefix) - len(timestamp) - 4
                        title = article.title[:title_space] + ("..." if len(article.title) > title_space else "")
                        try:
                            self.stdscr.addstr(current_y, 4, f"{prefix}{timestamp}{title}")
                        except curses.error:
                            pass

                        if article_idx == self.selected_index:
                            self.stdscr.attroff(curses.color_pair(3))
                        else:
                            self.stdscr.attroff(curses.color_pair(1))

                        current_y += 1

                    # Add spacing between feeds
                    current_y += 1

            else:  # brutalist view
                # Original brutalist view drawing code here...
                for i in range(min(WINDOW_SIZE, height - 3)):
                    idx = self.top_index + i
                    if idx >= len(self.filtered_articles):
                        break
                        
                    article = self.filtered_articles[idx]
                    y_pos = i * 2 + 2
                    
                    # Format article line
                    prefix = ""
                    if self.articles.index(article) in self.history:
                        prefix += "✓"
                    if self.articles.index(article) in self.bookmarks:
                        prefix += "★"
                    prefix = f"[{idx+1}]{prefix or ' '}"
                    
                    if idx == self.selected_index:
                        self.stdscr.attron(curses.color_pair(3))
                    else:
                        self.stdscr.attron(curses.color_pair(1))
                        
                    max_title_len = width - len(prefix) - 1
                    truncated_title = article.title[:max_title_len]
                    title_line = f"{prefix}{truncated_title}"
                    
                    try:
                        self.stdscr.addstr(y_pos, 0, title_line)
                    except curses.error:
                        pass
                        
                    if idx == self.selected_index:
                        self.stdscr.attroff(curses.color_pair(3))
                    else:
                        self.stdscr.attroff(curses.color_pair(1))
                    
                    if y_pos + 1 < height - 1:
                        try:
                            self.stdscr.attron(curses.color_pair(2))
                            source_line = f"  Source: {article.source}"
                            self.stdscr.addstr(y_pos + 1, 0, source_line[:width-1])
                            self.stdscr.attroff(curses.color_pair(2))
                        except curses.error:
                            pass

            self.draw_status_bar()

    def show_help(self):
        """Display help screen."""
        help_text = """
        Navigation:
        ↑/k      - Move up one article
        ↓/j      - Move down one article
        SHIFT+K  - Scroll window up
        SHIFT+J  - Scroll window down
        PgUp/PgDn- Move by pages
        
        Views:
        m        - Cycle through Brutalist/RSS/Anna's Archive modes
        a        - Switch to arXiv browser
        f        - Manage RSS feeds
        v        - View article in terminal
        ENTER    - Generate PDF
        
        Anna's Archive:
        /        - New search
        ENTER    - View item details
        j/k      - Navigate results
        q        - Return to main view
        
        Filtering & Search:
        /        - Search in titles/URLs
        s        - Filter by source
        t        - Filter by tags
        ESC      - Clear filter
        
        Article Management:
        b        - Toggle bookmark
        r        - Refresh articles/feeds
        S        - Show source statistics
        o        - Open in browser
        
        Display Options:
        c        - Cycle through title colors
        
        Article View Mode:
        ↑/↓      - Scroll text
        p        - Generate PDF
        q        - Return to list
        
        Indicators:
        ✓        - Read article/paper
        ★        - Bookmarked/Favorite
        """
        self.stdscr.clear()
        try:
            self.stdscr.addstr(0, 0, help_text)
        except curses.error:
            pass
        self.stdscr.refresh()
        self.stdscr.getch()

    def show_article_content(self, article_idx):
        """Display article content in the terminal."""
        if 0 <= article_idx < len(self.filtered_articles):
            article = self.filtered_articles[article_idx]
            content = scrape_article(article.url)
            
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            lines = []
            
            # Format title block
            lines.extend([
                "=" * width,
                "",
                article.title.center(width),
                "",
                "=" * width,
                "",
                f"Source: {article.source}",
                f"URL: {article.url}",
                "-" * width,
                ""
            ])
            
            # Format content
            if content:
                # Wrap content to terminal width
                words = content.split()
                current_line = []
                current_length = 0
                
                for word in words:
                    if current_length + len(word) + 1 <= width:
                        current_line.append(word)
                        current_length += len(word) + 1
                    else:
                        lines.append(" ".join(current_line))
                        current_line = [word]
                        current_length = len(word)
                
                if current_line:
                    lines.append(" ".join(current_line))
            
            # Display with scrolling
            scroll_pos = 0
            while True:
                self.stdscr.clear()
                
                # Show content
                for i in range(height - 2):
                    if scroll_pos + i < len(lines):
                        try:
                            self.stdscr.addstr(i, 0, lines[scroll_pos + i][:width-1])
                        except curses.error:
                            pass
                
                # Show status bar
                status = " Press q to return, ↑/↓ to scroll"
                try:
                    self.stdscr.attron(curses.A_REVERSE)
                    self.stdscr.addstr(height-1, 0, status.ljust(width-1))
                    self.stdscr.attroff(curses.A_REVERSE)
                except curses.error:
                    pass
                
                self.stdscr.refresh()
                
                key = self.stdscr.getch()
                if key == ord('q'):
                    break
                elif key in [ord('j'), curses.KEY_DOWN]:
                    scroll_pos = min(scroll_pos + 1, max(0, len(lines) - height + 2))
                elif key in [ord('k'), curses.KEY_UP]:
                    scroll_pos = max(0, scroll_pos - 1)

    def manage_feeds(self):
        """Enhanced RSS feed management interface."""
        selected_index = 0
        scroll_offset = 0
        
        while True:
            self.stdscr.clear()
            height, width = self.stdscr.getmaxyx()
            
            # Draw header with style
            header = "═══ RSS Feed Management ═══"
            self.stdscr.attron(curses.color_pair(1))
            self.stdscr.addstr(0, (width - len(header)) // 2, header)
            self.stdscr.attroff(curses.color_pair(1))
            
            # Show options with improved formatting
            options = [
                ("a", "Add new feed"),
                ("r", "Remove feed"),
                ("u", "Update all feeds"),
                ("f", "Force refresh selected feed"),
                ("t", "Test feed URL"),
                ("s", "Show feed statistics"),
                ("q", "Return to main view")
            ]
            
            for i, (key, desc) in enumerate(options):
                self.stdscr.attron(curses.color_pair(2))
                self.stdscr.addstr(i + 2, 2, key)
                self.stdscr.attroff(curses.color_pair(2))
                self.stdscr.addstr(i + 2, 4, f"- {desc}")
            
            # Show current feeds with scrolling
            if self.feed_manager.feeds:
                feed_start_y = len(options) + 3
                self.stdscr.addstr(feed_start_y, 0, "Current feeds:")
                self.stdscr.addstr(feed_start_y + 1, 0, "─" * width)
                
                feeds = list(self.feed_manager.feeds.items())
                max_visible_feeds = height - feed_start_y - 4
                visible_feeds = feeds[scroll_offset:scroll_offset + max_visible_feeds]
                
                for i, (name, url) in enumerate(visible_feeds):
                    y = feed_start_y + 2 + i
                    # Highlight selected feed
                    if scroll_offset + i == selected_index:
                        self.stdscr.attron(curses.color_pair(3))
                    
                    try:
                        entry = f"{name}: {url}"
                        if len(entry) > width - 4:
                            entry = entry[:width-7] + "..."
                        self.stdscr.addstr(y, 2, entry)
                    except curses.error:
                        pass
                    
                    if scroll_offset + i == selected_index:
                        self.stdscr.attroff(curses.color_pair(3))
                
                # Show scroll indicators if needed
                if scroll_offset > 0:
                    self.stdscr.addstr(feed_start_y + 2, width-3, "↑")
                if scroll_offset + max_visible_feeds < len(feeds):
                    self.stdscr.addstr(min(height-2, feed_start_y + max_visible_feeds + 1), width-3, "↓")
            
            # Show status bar
            if self.status_message:
                try:
                    self.stdscr.attron(curses.A_REVERSE)
                    self.stdscr.addstr(height-1, 0, self.status_message[:width-1].ljust(width-1))
                    self.stdscr.attroff(curses.A_REVERSE)
                except curses.error:
                    pass
            
            self.stdscr.refresh()
            
            # Handle input
            key = self.stdscr.getch()
            feeds = list(self.feed_manager.feeds.items())
            
            if key == ord('q'):
                break
            elif key == ord('a'):
                self.add_feed()
            elif key == ord('r'):
                if feeds:
                    name = feeds[selected_index][0]
                    self.feed_manager.remove_feed(name)
                    self.status_message = f"Removed feed: {name}"
                    selected_index = min(selected_index, len(feeds) - 2)
                    self.load_data()
            elif key == ord('u'):
                self.status_message = "Updating all feeds..."
                self.stdscr.refresh()
                self.feed_manager.update_all_feeds()
                self.load_data()
                self.status_message = "All feeds updated successfully"
            elif key == ord('f'):
                if feeds:
                    name, url = feeds[selected_index]
                    self.status_message = f"Refreshing feed: {name}..."
                    self.stdscr.refresh()
                    self.feed_manager.update_feed(name, url)
                    self.load_data()
                    self.status_message = f"Feed {name} refreshed successfully"
            elif key == ord('t'):
                if feeds:
                    name, url = feeds[selected_index]
                    self.status_message = f"Testing feed: {name}..."
                    self.stdscr.refresh()
                    if self.feed_manager.get_feed_content(url):
                        self.status_message = f"Feed {name} is valid and accessible"
                    else:
                        self.status_message = f"Error: Could not access feed {name}"
            elif key == ord('s'):
                self.show_feed_stats()
            elif key in [ord('\n'), ord('o')]:  # Enter or 'o' to open feed
                if feeds:
                    name, url = feeds[selected_index]
                    self.view_feed_articles(name, url)
            elif key in [ord('j'), curses.KEY_DOWN] and feeds:
                selected_index = min(selected_index + 1, len(feeds) - 1)
                if selected_index >= scroll_offset + max_visible_feeds:
                    scroll_offset += 1
            elif key in [ord('k'), curses.KEY_UP] and feeds:
                selected_index = max(selected_index - 1, 0)
                if selected_index < scroll_offset:
                    scroll_offset -= 1

    def add_feed(self):
        """Add a new RSS feed with improved input handling."""
        height, width = self.stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)  # Show cursor
        
        # Clear any previous status messages
        self.stdscr.move(height-3, 0)
        self.stdscr.clrtoeol()
        self.stdscr.move(height-2, 0)
        self.stdscr.clrtoeol()
        
        try:
            # Get feed name
            self.stdscr.addstr(height-3, 0, "Enter feed name: ")
            name = self.stdscr.getstr().decode('utf-8').strip()
            
            # Get feed URL
            self.stdscr.addstr(height-2, 0, "Enter feed URL: ")
            url = self.stdscr.getstr().decode('utf-8').strip()
            
        finally:
            curses.noecho()
            curses.curs_set(0)  # Hide cursor
        
        if name and url:
            # Show loading indicator
            self.status_message = "Adding feed... Please wait"
            self.stdscr.refresh()
            
            if self.feed_manager.add_feed(name, url):
                self.status_message = f"Added feed: {name}"
                self.load_data()  # Refresh data after adding feed
            else:
                self.status_message = "Error: Invalid RSS feed URL"

    def remove_feed(self):
        """Remove an RSS feed."""
        height, width = self.stdscr.getmaxyx()
        curses.echo()
        
        self.stdscr.addstr(height-2, 0, "Enter feed name to remove: ")
        name = self.stdscr.getstr().decode('utf-8').strip()
        
        curses.noecho()
        
        if name in self.feed_manager.feeds:
            self.feed_manager.remove_feed(name)
            self.status_message = f"Removed feed: {name}"
            self.load_data()  # Refresh data after removing feed

    def run(self):
        """Main run loop."""
        curses.start_color()
        curses.use_default_colors()
        curses.curs_set(0)  # Hide cursor
        self.colors.setup_colors()
        
        # Initialize viewers
        arxiv_manager = ArxivManager(self.feed_manager.db_conn)
        arxiv_viewer = ArxivViewer(self.stdscr, arxiv_manager, self.colors)
        self.annas_viewer = AnnasArchiveViewer(self.stdscr, self.colors)
        
        while True:
            if self.view_mode == 'arxiv':
                self.status_message = "Loading arXiv papers..."
                self.stdscr.refresh()
                arxiv_viewer.run()
                self.view_mode = 'brutalist'  # Return to main view after exiting arXiv browser
                continue
            elif self.view_mode == 'annas':
                self.status_message = "Loading Anna's Archive search..."
                self.stdscr.refresh()
                self.annas_viewer.run()
                self.view_mode = 'brutalist'  # Return to main view after exiting
                continue
                
            self.draw_articles()
            key = self.stdscr.getch()
            
            if key == ord('q'):
                break
            elif key == ord('a'):  # Switch to arXiv mode
                self.view_mode = 'arxiv'
                continue
                
            elif key in [ord('j'), curses.KEY_DOWN]:
                self.selected_index = min(self.selected_index + 1, len(self.filtered_articles) - 1)
                if self.selected_index >= self.top_index + WINDOW_SIZE:
                    self.top_index += 1
                    
            elif key in [ord('k'), curses.KEY_UP]:
                self.selected_index = max(self.selected_index - 1, 0)
                if self.selected_index < self.top_index:
                    self.top_index = max(self.top_index - 1, 0)
                    
            elif key == ord('J'):  # SHIFT+J
                self.top_index = min(self.top_index + WINDOW_SIZE, 
                                   len(self.filtered_articles) - WINDOW_SIZE)
                self.selected_index = self.top_index
                
            elif key == ord('K'):  # SHIFT+K
                self.top_index = max(self.top_index - WINDOW_SIZE, 0)
                self.selected_index = self.top_index
                
            elif key == ord('v'):  # View article
                if self.filtered_articles:
                    self.show_article_content(self.selected_index)
                    save_to_history(self.selected_index)
                    
            elif key == ord('o'):  # Open in browser
                if self.filtered_articles:
                    article = self.filtered_articles[self.selected_index]
                    webbrowser.open(article.url)
                    save_to_history(self.selected_index)
                    
            elif key == ord('m'):  # Toggle view mode
                # Cycle through view modes: brutalist -> rss -> annas -> brutalist
                if self.view_mode == 'brutalist':
                    self.view_mode = 'rss'
                elif self.view_mode == 'rss':
                    self.view_mode = 'annas'
                else:
                    self.view_mode = 'brutalist'
                    
                if self.view_mode != 'annas':
                    self.load_data()
                self.status_message = f"Switched to {self.view_mode.upper()} view"
                    
            elif key == ord('f'):  # Manage feeds
                self.manage_feeds()
                    
            elif key == ord('c'):  # Cycle colors
                self.colors.cycle_title_color()
                
            elif key in [ord('/'), ord('s'), ord('t')]:  # Search/filter
                self.filter_mode = {
                    ord('/'): 'search',
                    ord('s'): 'source',
                    ord('t'): 'tag'
                }[key]
                
                curses.echo()
                curses.curs_set(1)  # Show cursor
                self.stdscr.addstr(0, 0, f"{self.filter_mode.title()}: ".ljust(curses.COLS))
                self.filter_text = self.stdscr.getstr().decode('utf-8').strip()
                curses.noecho()
                curses.curs_set(0)  # Hide cursor
                
                self.apply_filter()
                
            elif key == 27:  # ESC - Clear filter
                self.filter_text = ""
                self.filter_mode = None
                self.filtered_articles = self.articles.copy()
                
            elif key == ord('p') or key == ord('\n'):  # Generate PDF
                if self.filtered_articles:
                    try:
                        curses.nocbreak()
                        curses.echo()
                        curses.endwin()

                        # Process the PDF
                        article = self.filtered_articles[self.selected_index]
                        content = scrape_article(article.url)
                        formatted_text = f"Title: {article.title}\nURL: {article.url}\n\nContent:\n{content}"

                        try:
                            subprocess.run(
                                ['/usr/local/bin/wpdf', formatted_text],
                                capture_output=True,
                                text=True,
                                check=True
                            )
                            print("\nPDF generated successfully")
                            idx = self.articles.index(article)
                            save_to_history(idx)
                        except subprocess.CalledProcessError as e:
                            print(f"\nError generating PDF: {e}")

                        print("\nPress Enter to continue...")
                        input()
                    finally:
                        # Restore terminal to curses mode
                        self.stdscr = curses.initscr()
                        curses.noecho()
                        curses.cbreak()
                        self.stdscr.keypad(1)
                        curses.start_color()
                        self.colors.setup_colors()
                        curses.curs_set(0)
                
            elif key == ord('b'):  # Toggle bookmark
                self.toggle_bookmark()
                
            elif key == ord('S'):  # Show stats
                self.show_source_stats()
                
            elif key == ord('r'):  # Refresh
                if self.view_mode == 'brutalist':
                    self.articles = self.fetch_fresh_articles()
                else:
                    self.feed_manager.update_all_feeds()
                self.filtered_articles = self.articles.copy()
                self.status_message = "Content refreshed"
                
            elif key == ord('?'):  # Help
                self.show_help()

        self.cleanup()

def ensure_output_dir():
    """Create output directory if it doesn't exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def load_cached_links():
    """Load links from cache file."""
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE) as f:
                data = json.load(f)
                return [(item['title'], item['url']) for item in data['links']]
        return []
    except Exception:
        return []

def save_links_to_cache(links):
    """Save links to cache file."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'links': [{'title': title, 'url': url} for title, url in links]
        }
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache_data, f, indent=2)
    except Exception:
        pass

def load_history():
    """Load read article history."""
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE) as f:
                return json.load(f).get('history', [])
        return []
    except Exception:
        return []

def save_to_history(index):
    """Save article to read history."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = load_history()
        if index not in history:
            history.append(index)
        with open(HISTORY_FILE, 'w') as f:
            json.dump({'history': history}, f, indent=2)
    except Exception:
        pass

def extract_links_from_brutalist():
    """Extract links from Brutalist Report."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get("https://brutalist.report", headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        
        # Find all article links
        for a in soup.find_all('a', href=True):
            href = a['href']
            if (not href.startswith(('#', '/', 'javascript:')) and 
                'brutalist.report' not in href and
                'login' not in href.lower()):
                title = a.get_text().strip()
                if title:  # Only add if there's an actual title
                    links.append((title, href))
        
        # Remove duplicates while preserving order
        seen = set()
        unique_links = []
        for title, url in links:
            if url not in seen:
                seen.add(url)
                unique_links.append((title, url))
                
        save_links_to_cache(unique_links)
        return unique_links
    except Exception as e:
        print(f"Error fetching articles: {e}")
        return []

def scrape_article(url):
    """Scrape article content from URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove unwanted elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'iframe', 
                           'aside', 'form', '.advertisement', '#cookie-notice', 
                           '.social-share', '.comments']):
            element.decompose()
        
        # Try to find the main content
        content = (
            soup.find('article') or 
            soup.find('main') or 
            soup.find('div', class_='content') or
            soup.find('div', class_='article-content') or
            soup.find('div', class_='entry-content') or
            soup.find('div', {'id': 'content'}) or
            soup.find('div', class_='post-content')
        )
        
        if content:
            # Clean up the text
            text = []
            for p in content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                text.append(p.get_text().strip())
            
            clean_text = ' '.join(text)
            clean_text = clean_text.replace('\n', ' ').strip()
            clean_text = ' '.join(clean_text.split())  # Remove extra whitespace
            return clean_text
            
        return "Could not extract content from this page."
        
    except Exception as e:
        return f"Error accessing article: {str(e)}. Please check your connection."

def main():
    """Main entry point with improved error handling."""
    ensure_output_dir()
    
    def run_viewer(stdscr):
        try:
            # Initialize curses
            curses.start_color()
            curses.use_default_colors()
            curses.noecho()
            curses.cbreak()
            stdscr.keypad(1)
            curses.curs_set(0)
            
            # Create and run viewer
            viewer = ArticleViewer(stdscr)
            viewer.run()
            
        except KeyboardInterrupt:
            pass
        except Exception as e:
            # Ensure terminal is restored before printing error
            try:
                stdscr.clear()
                stdscr.refresh()
                curses.endwin()
            except:
                pass
            print(f"\nAn error occurred: {e}")
        finally:
            # Cleanup terminal state
            try:
                # Reset terminal settings
                curses.nocbreak()
                curses.echo()
                stdscr.keypad(0)
                stdscr.clear()
                stdscr.refresh()
            except:
                pass
            try:
                curses.endwin()
            except:
                pass

    try:
        # Use wrapper to handle terminal setup/cleanup
        curses.wrapper(run_viewer)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        try:
            curses.endwin()
        except:
            pass

if __name__ == "__main__":
    main()