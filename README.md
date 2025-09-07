first want to create -python3 -m venv venv

next activate-source venv/bin/activate

next step - pip install -r requirements.txt - to install dependencies

next want to run the program -uvicorn main:app --reload

To post the pdf -curl -X POST "http://127.0.0.1:8000/upload-invoice/" -F "file=@"
