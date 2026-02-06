# Website Traffic Logger – AWS Lambda (Gold Version)

This AWS Lambda function logs website visits into DynamoDB and sends email
notifications for genuine visitors.

## Features
- Exact-IP blocking (no heuristics)
- Real client IP via X-Forwarded-For
- Geo lookup (city, state)
- DynamoDB logging
- SES email alerts
- Clean, stable schema

## DynamoDB Schema
- c_ip (partition key)
- timestamp (sort key)
- site
- user_agent
- os
- browser
- location

## Notes
This repository contains the **Gold Lambda Code** – the canonical, known-good version.
