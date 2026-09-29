import sys
import os

# Ensure package root is in sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from fkplayer.player import main

if __name__ == "__main__":
    main()
