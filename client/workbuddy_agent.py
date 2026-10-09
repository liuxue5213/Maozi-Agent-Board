"""PyInstaller 打包入口：python3 workbuddy_agent.py [run|once|ports ...]"""
import sys

from workbuddy.__main__ import main

if __name__ == "__main__":
    sys.argv[0] = "workbuddy-agent"
    sys.exit(main(sys.argv[1:] or ["run"]))
