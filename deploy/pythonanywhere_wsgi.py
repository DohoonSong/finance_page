"""Copy this into the WSGI configuration file linked from the Web tab."""
import os
import sys
from pathlib import Path

PROJECT = Path('/home/YOURUSERNAME/finance')
sys.path.insert(0, str(PROJECT))

from dotenv import load_dotenv
load_dotenv(PROJECT / '.env')
os.environ['APP_ENV'] = 'production'

from finance import create_app
application = create_app()
