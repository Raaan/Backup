# v0.1.0 — Personal backup edition

This first packaged version preserves a personal VPS setup developed through several years of use. The reusable installer and modular organization were completed recently. Following the blocking experienced last month, the project was moved to GitHub for preservation and backup.

## Improvements included

- More reliable fresh-server installation and configuration input.
- Automatic public-address detection and clearer timezone handling.
- Automatic user alignment across naming variations and traffic sources.
- Correct registration of new accounting identities and safer account retirement.
- Improved optional email setup and consistent GB/GiB reporting.
- Expanded regression coverage and deployment verification.

## Verification

34 automated tests passed on Ubuntu. Protocol requests, traffic ingestion, database integrity, and a monthly rollover simulation passed on a test VPS.

Natural monthly rollover and certificate replacement at expiry remain to be observed. Simulation results do not establish those outcomes. Detailed fixes and operational records are maintained privately.
