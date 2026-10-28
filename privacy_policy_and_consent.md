# Matra — Privacy Policy & Consent Notice

> **Last updated:** October 2026

---

## 1. Who we are

Matra is a personal prescription-assistant application that helps you understand your medicines and build a dosing schedule from your prescription.

---

## 2. What data we collect and why

| Data | Purpose | Retained |
|---|---|---|
| Prescription image or PDF | Extract medicine names, dosages, and instructions | Up to 30 days, encrypted at rest |
| Handwriting line crops | Display original handwriting alongside extractions | Up to 30 days, encrypted at rest |
| Optional packet photos | Provide high-confidence printed text hints for unreadable lines | Up to 30 days, encrypted at rest |
| Optional pharmacist export | Enable consulting your pharmacist with a de-identified snippet | Time-limited (1-48h), zero patient PII |
| Medicine names, dosages, schedule | Build and display your dosing plan | Up to 30 days |
| Dose-taken / dose-skipped logs | Show your adherence timeline | Up to 30 days |
| Reminder fire / snooze events | Deliver reminders; do not repeat snoozed alerts | Up to 30 days |
| Anonymous feedback (field corrections) | Improve extraction accuracy | Up to 30 days |

We do **not** collect your name, national ID, phone number, email address, or any other personally-identifiable information (PII).  
We do **not** use cookies or track you across other sites or apps.

---

## 3. Encryption at rest

Every prescription image, PDF, handwriting line crop, and optional packet photo you upload is **encrypted immediately** using AES-256 (Fernet symmetric encryption) before being written to disk.  
The decryption key is stored separately from the data and is never logged. Line crops and packet photos are cascade-deleted immediately whenever the prescription is deleted.

---

## 4. What we do NOT log

Our system is designed under a strict no-logging-of-health-data policy:

- Medicine names, strengths, and dosages are **never written to application logs**.
- Your name, doctor's name, or any other text extracted from the prescription is **never written to logs**.
- All log lines pass through a PII/health-data redaction filter before being emitted.

---

## 5. Automatic 30-day purge

All data (encrypted image, extracted medicines, schedule, dose logs, reminders, feedback) is **automatically purged 30 days** after the prescription was uploaded.

An administrator may also trigger an early purge for a specific retention window (e.g., 7 days) via the `/admin/purge` endpoint.

---

## 6. Your right to instant deletion

You can permanently delete your prescription and all associated data at any time by:

- Tapping **Delete Prescription** in the app, or
- Sending `DELETE /prescriptions/{id}` from an authenticated client.

This deletes:
- The encrypted image file from disk.
- All extracted-data, matched-drug, and schedule records from the database.
- All dose-log and reminder records.
- Any feedback reports tied to that prescription.

Deletion is **immediate and irreversible.**

---

## 7. No medical advice

> ⚠️ **Matra is not a substitute for professional medical advice.**  
> All schedules and explanations are informational only.  
> Always confirm dosing with your doctor or pharmacist before taking any medicine.

Every results screen in the app displays this disclaimer.

---

## 8. Data sharing

We do **not** sell, rent, or share your data with third parties.  
The prescription image is transmitted to **Anthropic Claude API** (a third-party vision AI) solely to extract text from the image. Claude's data handling is governed by Anthropic's privacy policy.

---

## 9. Changes to this policy

If we make material changes, we will notify you in the app before the changes take effect.

---

## 10. Consent

By uploading a prescription you confirm that:

- [ ] I understand that Matra is not a medical service and the schedules are suggestions.
- [ ] I understand my prescription image is sent to an AI service for text extraction.
- [ ] I have read and agree to the 30-day data retention and automatic purge policy.
- [ ] I understand I can delete my data at any time.

---

*Matra — prescription assistant — Phase 6 Beta*
