# ⚙️ WorkByte Backend

Backend service for **WorkByte**, handling authentication, job matching, contracts, and AI-powered moderation.

---

## 📌 Overview

This repository contains the backend of WorkByte, including:

* Authentication (JWT + Google OAuth 2.0)
* Job marketplace, proposals & contract lifecycle
* AI features: job fit analysis, harmful text detection, CV analysis, scam detection, review analysis
* Admin moderation & dashboard

---

## 🏗️ Tech Stack

* **Framework**: FastAPI (Python 3.12)
* **Database**: PostgreSQL + pgvector
* **Containerization**: Docker
* **Authentication**: JWT + Google OAuth 2.0
* **File Storage**: Supabase Storage
* **Push Notifications**: Firebase Cloud Messaging (FCM)
* **LLM**: GROQ API
* **Embedding Model**: nomic-ai/nomic-embed-text-v1.5

---

## 🐳 Development

All development is done inside the Docker container environment.

---

## 🔗 Related Repositories

* Backend: https://github.com/hwasyui/WorkByte-BACKEND
* Frontend: https://github.com/hwasyui/WorkByte-FRONTEND
* Database: https://github.com/hwasyui/WorkByte-DATABASE
* Storage: https://github.com/hwasyui/WorkByte-STORAGE

---

## 👥 Team Members & Commit Codes

| Code  | Name           | GitHub                            |
| ----- | -------------- | --------------------------------- |
| [ASW] | hwasyui        | https://github.com/hwasyui        |
| [IKP] | tannpsy        | https://github.com/tannpsy        |
| [SKF] | sarahkimberlyy | https://github.com/sarahkimberlyy |

---

## 📝 Notes

* Ensure environment variables are configured before starting (see `.env`)
* Use commit prefixes when contributing

---

## 📄 License

This project is for academic (capstone) purposes.
