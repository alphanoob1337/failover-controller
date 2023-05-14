FROM python:3.11.3-alpine

WORKDIR /usr/src/app

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy scripts
COPY controller.py ./

CMD [ "python", "./controller.py" ]