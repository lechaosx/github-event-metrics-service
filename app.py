import os
import threading
import time
import datetime
import logging
import bisect
import io

import matplotlib.pyplot
import requests
import fastapi

logging.basicConfig(level = logging.DEBUG)
logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

EVENT_TYPES = {"WatchEvent", "PullRequestEvent", "IssuesEvent"}

# Trivial in-memory event store
event_ids: set[str] = set()
events_by_type: dict[str, list[datetime.datetime]] = { event_type: [] for event_type in EVENT_TYPES }
prs_by_repo: dict[str, list[datetime.datetime]] = {}
event_store_lock: threading.Lock = threading.Lock()


def fetch_github_events(headers: dict) -> tuple[list[dict], int | None]:
	events: list[dict] = []
	poll_interval: int | None = None

	url: str | None = "https://api.github.com/events?per_page=100"
	while url:
		logger.debug(f"Fetching from URL: {url}")

		response = requests.get(url, headers = headers)

		if interval := response.headers.get("X-Poll-Interval"):
			poll_interval = int(interval)

		if response.ok:
			logger.debug(f"Successfully fetched {len(response.json())} events from {url}")
			events.extend(response.json())
		else:
			logger.error(f"Failed to fetch GitHub events: {response.status_code} {response.text}")

		url = response.links.get("next", {}).get("url")

	return events, poll_interval


def github_events_loop() -> None:
	headers: dict = {
		"Accept": "application/vnd.github+json",
		"X-GitHub-Api-Version": "2022-11-28",
		"User-Agent": "DatamoleAssignment/1.0"
	}

	if GITHUB_TOKEN:
		headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

	while True:
		logger.info("Polling GitHub events...")

		try:
			events, poll_interval = fetch_github_events(headers)
			logger.info(f"Fetched {len(events)} events.")

			ingested_events = 0
			with event_store_lock:
				for event in events:
					if event["type"] in EVENT_TYPES and event["id"] not in event_ids:
						ingested_events += 1

						event_ids.add(event["id"])

						created_at = datetime.datetime.strptime(event["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo = datetime.timezone.utc)

						bisect.insort(events_by_type.setdefault(event["type"], []), created_at)

						if event["type"] == "PullRequestEvent":
							bisect.insort(prs_by_repo.setdefault(event["repo"]["name"], []), created_at)

			logger.info(f"Ingested {ingested_events} new relevant events.")

		except Exception as e:
			logger.error(f"Error while polling GitHub events: {e}")

		sleep_time = poll_interval if poll_interval else 60

		logger.debug(f"Sleeping for {sleep_time} seconds.")

		time.sleep(sleep_time)


threading.Thread(target = github_events_loop, daemon = True).start()


app = fastapi.FastAPI()


@app.get("/metrics/average-pr-time")
def average_pr_time(repo_name: str):
	logger.info(f"Requested average PR interval for repo: '{repo_name}'")

	with event_store_lock:
		prs = prs_by_repo.get(repo_name, [])

	if len(prs) < 2:
		logger.warning(f"Not enough PR events for repo {repo_name}.")
		return { "average_seconds": None, "message": f"Only {len(prs)} PR event(s) found. At least 2 are required." }
	
	intervals = [(prs[i] - prs[i - 1]).total_seconds() for i in range(1, len(prs))]

	average_seconds = sum(intervals) / len(intervals)

	logger.info(f"Average PR interval for '{repo_name}': {average_seconds:.2f} seconds over {len(prs)} events.")
	
	return {"average_seconds": average_seconds}


@app.get("/metrics/event-counts")
def event_counts(offset_minutes: int = fastapi.Query(..., gt = 0)):
	logger.info(f"Requested event counts in the past {offset_minutes} minute(s).")

	cutoff: datetime.datetime = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes = offset_minutes)

	result = {}

	with event_store_lock:
		for event_type, event_list in events_by_type.items():
			index = bisect.bisect_left(event_list, cutoff)
			result[event_type] = len(event_list) - index

	logger.info(f"Event counts: {result}")
	
	return result

@app.get("/metrics/visualization")
def event_visualization(offset_minutes: int = fastapi.Query(60, gt = 0)):
	logger.info(f"Generating visualization for event counts in the past {offset_minutes} minute(s).")

	cutoff: datetime.datetime = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes = offset_minutes)

	event_counts_result = {}

	with event_store_lock:
		for event_type, event_list in events_by_type.items():
			index = bisect.bisect_left(event_list, cutoff)
			event_counts_result[event_type] = len(event_list) - index

	_, ax = matplotlib.pyplot.subplots()
	ax.bar(event_counts_result.keys(), event_counts_result.values(), color = ['blue', 'green', 'red'])

	ax.set_xlabel('Event Type')
	ax.set_ylabel('Count')
	ax.set_title(f'Event Counts in the Last {offset_minutes} Minutes')

	buffer = io.BytesIO()
	matplotlib.pyplot.savefig(buffer, format = "png")
	buffer.seek(0)
	
	return fastapi.responses.StreamingResponse(buffer, media_type="image/png")