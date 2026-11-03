import os
import oracledb
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "2484"))
DB_SERVICE_NAME = os.getenv("DB_SERVICE_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def get_connection():
    try:
        connection = oracledb.connect(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            service_name=DB_SERVICE_NAME,
            protocol="tcps"
        )

        print("================================")
        print("Database Connected Successfully!")
        print("================================")

        return connection

    except oracledb.Error as e:
        print("================================")
        print("Database Connection Failed!")
        print("================================")
        print("Error:", e)

        return None


if __name__ == "__main__":
    connection = get_connection()

    if connection:
        try:
            cursor = connection.cursor()

            cursor.execute("SELECT SYSDATE FROM DUAL")
            result = cursor.fetchone()

            print("Database Time:", result[0])

            cursor.close()

        finally:
            connection.close()
            print("Database Connection Closed.")