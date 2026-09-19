# IT Security Policy

Synthetic demo document — fictional company, not real data.

Lumen Labs Pty Ltd. Policy SEC-02, version 3.4, effective 1 April 2026. Owner: Chief Information Security Officer, Ravi Menon. This policy applies to all employees, contractors and anyone else with access to Lumen Labs systems or data.

## 1. Purpose

This policy sets the minimum security rules that protect Lumen Labs, our customers and their data. Breaching this policy may lead to disciplinary action, up to and including termination of employment.

## 2. Passwords

- Passwords must be at least 14 characters long. Passphrases of four or more random words are encouraged.
- Passwords must not reuse any of your last 10 passwords, and must never be reused across work and personal accounts.
- All passwords must be stored in the company password manager, Keyvault. Writing passwords down or saving them in browsers is not permitted.
- Passwords do not expire on a schedule. You must change a password immediately if you suspect it has been exposed.

## 3. Multi-Factor Authentication

Multi-factor authentication (MFA) is required on every company system that supports it, including email, the VPN, source code repositories and the Lumen Platform admin console. Use the Lumen Authenticator app or a hardware security key. SMS codes are not an accepted second factor.

Administrators and anyone with production access must use a hardware security key. The IT Service Desk issues two keys to each administrator: one for daily use and one kept in a safe place as a backup.

## 4. Device Security

- Company laptops must use full-disk encryption (FileVault on macOS, BitLocker on Windows). IT enables this before the laptop is issued.
- Screens must lock automatically after 5 minutes of inactivity.
- Operating system and browser updates must be installed within 14 days of release. Updates marked critical must be installed within 72 hours.
- Only software from the Lumen Software Catalogue may be installed. Other software needs approval from the IT Service Desk.
- Personal USB drives and other removable storage must not be connected to company devices.

## 5. Network and VPN

The company VPN, Lumen Connect, must be switched on whenever you work from a network that Lumen Labs does not control, including home networks, public Wi-Fi, hotels and airports. The VPN is required to reach internal systems such as PeopleHub, CaseDesk and the staging environment.

## 6. Incident Reporting

Report any suspected security incident within 1 hour of discovering it, by email to security@lumenlabs.example or in the #security-incidents Slack channel. Incidents include phishing emails you clicked on, malware warnings, unexpected MFA prompts and data sent to the wrong person.

Lost or stolen laptops and phones must also be reported within 1 hour so that IT can lock and wipe them remotely. Do not try to investigate an incident yourself.

## 7. Access Management

- Access is granted on a least-privilege basis and must be requested through the IT Service Desk with manager approval.
- Managers must review their team's access every quarter and remove anything no longer needed.
- When someone leaves Lumen Labs, all of their access is removed within 24 hours of their last working day.

## 8. Data Classification

Lumen Labs data is classified into four levels: Public, Internal, Confidential and Restricted. Customer data is always at least Confidential. Restricted data, such as production database credentials and encryption keys, may only be stored in Keyvault or the approved secrets manager and must never be shared in chat or email.

## 9. Exceptions

Exceptions to this policy must be approved in writing by the Chief Information Security Officer and are reviewed every 6 months.
