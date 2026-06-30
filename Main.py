# Compatibility wrapper for Replit users who created Main.py with a capital M.
# The real entry point is main.py.
import asyncio
from main import main

if __name__ == "__main__":
    asyncio.run(main())
