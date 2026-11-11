# Version history

The v0.0.x numbers below retrospectively label actual development milestones. They were not separate published releases. v0.1.0 is the first GitHub Release.

| Version | Improvements |
|---|---|
| v0.0.1 | Fixed early installation failures and improved first-run readiness checks. |
| v0.0.2 | Simplified installation, detected the server address automatically, and fixed timezone input and defaults. |
| v0.0.3 | Fixed integration failures between user management, account fusion, and traffic collection. Unified naming variations and corrected new-user registration. |
| v0.0.4 | Added account lifecycle synchronization: retain users while present in any protocol and retire removed users in the next billing period. |
| v0.0.5 | Fixed email setup input handling and clarified the independent mail workflow. |
| v0.0.6 | Fixed GB/GiB reporting conversion and labels; improved small-traffic display. Byte counters and quota calculations remained correct. |
| v0.1.0 | Packaged the working baseline, added a brief English overview and installation instructions, and published the first Release. |

The latest verified baseline passed 34 Ubuntu tests and test-environment protocol, ingestion, database-integrity, and monthly-simulation checks. Natural monthly rollover and certificate replacement at expiry still require observation.

Detailed diagnosis, recovery procedures, and operational records remain private.
