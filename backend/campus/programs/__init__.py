"""Degree program course lists for the Degree Progress page.

SJSU publishes each major's required courses only in catalog.sjsu.edu, which
sits behind an AWS WAF and has no API key we can get (decision no-acalog-key).
Many departments also list their program's courses on their own www.sjsu.edu
pages, each in its own layout. `sources.json` names one or more such pages per
program, `extract` reads course codes and titles out of them with one generic
reader, and `build` (an offline CLI) writes the result as static JSON the UI
loads. Nothing here runs inside a request.
"""
