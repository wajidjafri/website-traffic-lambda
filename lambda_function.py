import boto3
from aws_xray_sdk.core import xray_recorder, patch_all
patch_all()

import json
import http.client
import logging
from datetime import datetime
from dateutil import tz
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ----------------------------
# AWS clients
# ----------------------------
ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")

# ----------------------------
# Config
# ----------------------------
TABLE_NAME = "WebsiteTraffic"
GSI_NAME = "c_ip-timestamp-index"
SOURCE_EMAIL = "OracleDBA900@gmail.com"

# Block ONLY these exact IPs (do NOT store in DynamoDB)
BLOCKED_IPS = {
    "72.152.84.15",
    "72.152.84.13",
    "135.232.20.17",
    "135.232.20.19",
    "9.169.121.184",
    "9.169.121.185",
}

# Geo lookup
IP_API_HOST = "ip-api.com"
IP_API_TIMEOUT_SEC = 3

table = dynamodb.Table(TABLE_NAME)

# ----------------------------
# Lambda handler
# ----------------------------
def lambda_handler(event, context):
    visitor_ip = get_client_ip(event)

    qsp = event.get("queryStringParameters") or {}
    site = qsp.get("site", "unknown")

    headers = event.get("headers") or {}
    user_agent = headers.get("User-Agent") or headers.get("user-agent") or "Unknown"

    os_info, browser_info = parse_user_agent(user_agent)

    # decide blocked (only exact list)
    is_blocked = visitor_ip in BLOCKED_IPS

    # geo
    location_info = get_location_info(visitor_ip)

    # log always
    timestamp = get_est_time_now()
    put_visit(
        visitor_ip=visitor_ip,
        timestamp=timestamp,
        site=site,
        user_agent=user_agent,
        os_info=os_info,
        browser_info=browser_info,
        location_info=location_info,
    )

    # blocked => no email
    if is_blocked:
        logger.info(f"BLOCKED (no email): ip={visitor_ip}")
        return {"statusCode": 200, "body": "Logged (blocked IP, no email)."}

    # otherwise email
    all_visits = query_all_visits_by_ip(visitor_ip)
    send_email(visitor_ip, all_visits, os_info, browser_info, location_info, site)
    return {"statusCode": 200, "body": "Visitor logged and email sent successfully."}


# ----------------------------
# IP helper
# ----------------------------
def get_client_ip(event):
    headers = event.get("headers") or {}
    xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    try:
        return event["requestContext"]["http"]["sourceIp"]
    except Exception:
        return "unknown"


# ----------------------------
# UA parsing
# ----------------------------
@xray_recorder.capture("parse_user_agent")
def parse_user_agent(user_agent: str):
    browsers = ["Chrome", "Edg", "Edge", "Firefox", "Safari", "Opera", "MSIE", "Trident"]
    oss = ["Windows", "Macintosh", "Linux", "Android", "iPhone", "iPad", "iOS"]

    ua = user_agent or ""
    browser_info = "Unknown Browser"
    os_info = "Unknown OS"

    for b in browsers:
        if b in ua:
            browser_info = "Edge" if b in ("Edg",) else b
            break

    for o in oss:
        if o in ua:
            os_info = "iOS" if o in ("iPhone", "iPad") else o
            break

    return os_info, browser_info


# ----------------------------
# Time
# ----------------------------
@xray_recorder.capture("get_est_time_now")
def get_est_time_now():
    utc_now = datetime.utcnow().replace(tzinfo=tz.tzutc())
    est_now = utc_now.astimezone(tz.gettz("America/New_York"))
    return est_now.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------
# Geo lookup
# ----------------------------
@xray_recorder.capture("get_location_info")
def get_location_info(ip_address):
    if not ip_address or ip_address == "unknown":
        return "Location Unknown"
    try:
        conn = http.client.HTTPConnection(IP_API_HOST, timeout=IP_API_TIMEOUT_SEC)
        conn.request("GET", f"/json/{ip_address}")
        res = conn.getresponse()
        if res.status == 200:
            data = json.loads(res.read())
            city = data.get("city", "Unknown City")
            region = data.get("regionName", "Unknown State")
            return f"{city}, {region}"
    except Exception as e:
        logger.warning(f"Geo lookup failed for {ip_address}: {e}")
    return "Location Unknown"


# ----------------------------
# DynamoDB write (ONLY clean columns)
# ----------------------------
@xray_recorder.capture("put_visit")
def put_visit(visitor_ip, timestamp, site, user_agent, os_info, browser_info, location_info):
    item = {
        "c_ip": visitor_ip,
        "timestamp": timestamp,
        "site": site,
        "user_agent": user_agent,
        "os": os_info,
        "browser": browser_info,
        "location": location_info,
    }
    table.put_item(Item=item)


# ----------------------------
# DynamoDB read
# ----------------------------
def query_all_visits_by_ip(ip_address):
    resp = table.query(
        IndexName=GSI_NAME,
        KeyConditionExpression=Key("c_ip").eq(ip_address),
        ScanIndexForward=False
    )
    return resp.get("Items", [])


# ----------------------------
# Email (ONLY clean columns)
# ----------------------------
@xray_recorder.capture("send_email")
def send_email(visitor_ip, all_visits, os_info, browser_info, location_info, site):
    subject = f"[{site}] Visitor IP: {visitor_ip} - {location_info} - {os_info}/{browser_info}"

    html = []
    html.append(f"<h3>{subject}</h3>")
    html.append("<table border='1' cellpadding='4' cellspacing='0'>")
    html.append("<tr><th>Date</th><th>OS/Browser</th><th>Location</th><th>Site</th></tr>")

    for v in all_visits:
        ts = v.get("timestamp", "Unknown")
        os_v = v.get("os", os_info)
        br_v = v.get("browser", browser_info)
        loc_v = v.get("location", location_info)
        site_v = v.get("site", site)

        html.append(
            "<tr>"
            f"<td>{ts}</td>"
            f"<td>{os_v}/{br_v}</td>"
            f"<td>{loc_v}</td>"
            f"<td>{site_v}</td>"
            "</tr>"
        )

    html.append("</table>")
    html_content = "".join(html)

    ses.send_email(
        Source=SOURCE_EMAIL,
        Destination={"ToAddresses": [SOURCE_EMAIL]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Html": {"Data": html_content}}
        }
    )
