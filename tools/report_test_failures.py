"""Expose captured pytest failures through the GitHub Checks annotations API."""

import sys
import xml.etree.ElementTree as ET


def escape(value):
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


if __name__ == "__main__":
    for case in ET.parse(sys.argv[1]).iter("testcase"):
        for kind in ("failure", "error"):
            failure = case.find(kind)
            if failure is not None:
                name = case.get("name", "pytest")
                details = failure.text or failure.get("message", "Test failed")
                print(f"::error::{escape(name + chr(10) + details[:50000])}")
