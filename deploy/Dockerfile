# Use an official Python runtime as a parent image
FROM python:3.12-bullseye

# Set the working directory in the container
WORKDIR /app

# Copy dependency files for better Docker layer caching
COPY requirements.txt README.md ./

# Install dependencies using pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container
COPY . .

# Expose the desired port
EXPOSE 8765

# Run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
