import json
import boto3
import praw
import datetime
import nltk
from botocore.exceptions import ClientError
from nltk.sentiment import SentimentIntensityAnalyzer
import os
from decimal import Decimal

# Aseguramos que NLTK busque en la ruta del layer
nltk.data.path.append("/opt/python/nltk_data")

# Inicializamos VADER
VADER_ANALYZER = SentimentIntensityAnalyzer()

# Parámetros de AWS
REGION_NAME = 'eu-west-3'
SECRET_NAME = 'RedditPRAWCredentials'
TABLE_NAME = "SentimentObserverResults"

def get_secret():

    client = boto3.client(service_name='secretsmanager', region_name=REGION_NAME)
    try:
        response = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        print(f"Error: No se pudo obtener el secreto {SECRET_NAME}.")
        raise e
    
    secret_string = response['SecretString']
    return json.loads(secret_string)

def lambda_handler(event, context):

    #Inicializar dynamo
    dynamo = boto3.resource('dynamodb', region_name=REGION_NAME)
    dynamo_table = dynamo.Table(TABLE_NAME)

    # Obtenemos credenciales de PRAW desde Secrets Manager
    PRAW_CREDENTIALS = get_secret()
    
    # Creamos la conexión con PRAW
    reddit = praw.Reddit(
        client_id=PRAW_CREDENTIALS['client_id'],
        client_secret=PRAW_CREDENTIALS['client_secret'],
        user_agent="python:macos:sentiment.collector:v1.0 (by /u/JMatthewGutierrez)"
    )

    # Configuración de subreddit
    SUBREDDIT_NAME = "valencia"
    LIMIT_POSTS = 50

    # Obtenemos posts más populares
    posts = reddit.subreddit(SUBREDDIT_NAME).hot(limit=LIMIT_POSTS)

    processed_results = []
		
		# Procesamos los posts y los añadimos a la lista	
    for post in posts:
        score = VADER_ANALYZER.polarity_scores(post.title)

        processed_results.append({
            'SubredditName': SUBREDDIT_NAME,
            'Timestamp': datetime.datetime.now().isoformat(),
            'PostID': post.id,
            'Title': post.title,
            'SentimentScore': float(score['compound']),
            'SentimentLabel': 'Positive' if score['compound'] >= 0.05 
                              else ('Negative' if score['compound'] <= -0.05 else 'Neutral')
        })

        print(f"Posts procesados y listos para carga: {len(processed_results)}")
    
    # Carga a DynamoDB usando Batch Writer
    try:
        with dynamo_table.batch_writer() as batch:
            for item in processed_results:
                # Paso de SentimentScore a decimal
                item['SentimentScore'] = Decimal(str(item['SentimentScore']))
                batch.put_item(Item=item)

        print(f"Carga exitosa a DynamoDB: {len(processed_results)} elementos.")

    except ClientError as e:
        print(f"ERROR: Fallo al escribir en DynamoDB. {e}")
        # La función debe fallar si la carga no es crítica.
        raise e

    # Creamos lista de títulos para el return
    post_titles = [p['Title'] for p in processed_results]

    return {
        'statusCode': 200,
        # Devolvemos solo un mensaje y el conteo de postsr
        'body': json.dumps({'posts_count': len(processed_results), 'status': 'Pipeline executed successfully and data loaded to DynamoDB and S3!'})    
    }