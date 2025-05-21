# YouTube Channel Analyzer (Streamlit)

Analyze any public YouTube channel and download a multi-sheet Excel report.

## Features
- Pulls every video via YouTube Data API v3  
- Classifies uploads as **Short** or **Long** (default ≤ 180 s)  
- Calculates monthly summaries and view-count brackets  
- Lists the top 20 videos by views  
- Exports an Excel workbook with four analytical sheets  
- Runs entirely in the browser through Streamlit

## Prerequisites
- Python 3.9+
- A valid YouTube Data API key

## Quick start
```bash
git clone https://github.com/your-user/your-repo.git
cd your-repo
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
