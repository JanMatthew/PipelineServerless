# Pipeline Serverless de Análisis de Sentimiento — Documentación Técnica

# Pipeline Serverless de Análisis de Sentimiento — Documentación Técnica

---

### 1. Resumen ejecutivo

- Pipeline **ETL serverless** en **AWS** para ingesta en tiempo real desde Reddit.
- Análisis de sentimiento con **NLTK VADER** sobre títulos de posts.
- Persistencia en **DynamoDB** con escrituras batch para optimizar coste y rendimiento.
- Diseño **escalable, automatizado y de bajo coste**.

---

### 2. Arquitectura del sistema

- Orquestación y cómputo: **AWS Lambda** + **EventBridge**
- Seguridad: **AWS Secrets Manager**
- Persistencia: **AWS DynamoDB**
- Opcional: **Amazon S3** como data lake

![image.png](Pipeline%20Serverless%20de%20An%C3%A1lisis%20de%20Sentimiento%20%E2%80%94%20D/image.png)

> Flujo general
> 

> 1) **AWS EventBridge** actúa como *scheduler*, invocando la función Lambda cada hora.
> 

> 2) Lambda obtiene credenciales de Secrets Manager
> 

> 3) Extrae posts con **PRAW** (Reddit API)
> 

> 4) Calcula **compound score** con VADER y clasifica sentimiento
> 

> 5) Escribe resultados en **DynamoDB** con `batch_writer()`
> 

---

### 3. Componentes técnicos

| Componente | Función | Tecnología |
| --- | --- | --- |
| Fuente de datos | Ingesta de posts populares (hot) | Reddit API (PRAW) |
| Compute y orquestación | Lógica ETL y scheduler | AWS Lambda + EventBridge |
| Seguridad | Gestión de credenciales | AWS Secrets Manager |
| Procesamiento | Análisis de sentimiento | NLTK VADER |
| Base de datos | Almacenamiento rápido de resultados | AWS DynamoDB |

---

### 4. Flujo ETL

- Extract
    - Conexión a **Reddit API** mediante **PRAW** usando credenciales de **Secrets Manager**.
    - Extracción de los **50 posts** más populares del subreddit objetivo.
- Transform
    - Cálculo de **compound_score** con **VADER** en rango [-1, 1].
    - Asignación de etiqueta: Positive, Negative o Neutral.
- Load
    - Conversión de floats a **Decimal** para compatibilidad con DynamoDB.
    - Escritura **batch** con `batch_writer()`.

---

### 5. Decisiones de ingeniería clave

| Decisión | Descripción | Impacto |
| --- | --- | --- |
| Uso de Secrets Manager | Credenciales fuera del código (client_id, client_secret, username, password). | Mejores prácticas de seguridad y cumplimiento. |
| Inicialización fuera del handler | Clientes Boto3 y analizador VADER se crean fuera de `lambda_handler`. | Menor latencia en warm starts. |
| Docker para layers | Compilación de dependencias (PRAW, NLTK) en contenedor Linux. | Evita errores de importación y asegura despliegue reproducible. |
| Conversión a Decimal | Adaptación del tipo numérico a DynamoDB. | Compatibilidad y precisión. |
| Carga con `batch_writer` | Inserción de múltiples ítems en una llamada. | Mejor rendimiento y menor coste. |

---

### 6. Diseño de base de datos (DynamoDB)

**Tabla:** `SentimentObserverResults`

| Atributo | Tipo | Función |
| --- | --- | --- |
| PostID | String | <strong>Partition Key (PK)</strong> — Identificador único del post. |
| Timestamp | String | <strong>Sort Key (SK)</strong> — Fecha/hora ISO 8601 de la ingesta. |
| Title | String | Título del post. |
| SentimentScore | Number (Decimal) | Puntuación compuesta de VADER. |
| SentimentLabel | String | Positive, Negative o Neutral. |
| SubredditName | String | Nombre del subreddit origen. |

---

### 7. Implementación (código)

- Lenguaje: **Python 3.10**
- Dependencias: `praw`, `boto3`, `nltk`
- Archivo principal: `lambda_[function.py](http://function.py)`
- Responsabilidades: autenticación, extracción, análisis y carga.

```python
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
```

---

### 8. Seguridad e IAM

- Rol de ejecución de Lambda con permisos mínimos:
    - `secretsmanager:GetSecretValue`
    - `dynamodb:BatchWriteItem`
    - `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`
- Secret `RedditPRAWCredentials` en **Secrets Manager** accesible vía **Boto3**.

---

### 9. Pruebas y validación

- Pruebas realizadas
    - Ejecución manual de Lambda con evento vacío.
    - Verificación de 50 posts cargados en DynamoDB.
    - Validación de `compound_score` en rango [-1, 1].
    - Revisión de logs en CloudWatch.
- Resultados esperados
    - `statusCode: 200`
    - Mensaje: "Pipeline executed successfully and data loaded to DynamoDB!"

---

### 10. Conclusión y mejoras futuras

- Arquitectura **serverless** que combina ingesta, análisis NLP y persistencia.
- Escalable, segura y eficiente en coste.
- Próximos pasos
    - Almacenamiento crudo en **S3**.
    - Visualización con **QuickSight** o **Grafana**.
    - Alertas con **SNS** o **SES**.

---

### 11. Créditos y autoría

- Autor: **Jan Matthew Pareja Gutiérrez**
- Fecha: 10 de noviembre de 2025
- Contacto
    - LinkedIn:
    
    [www.linkedin.com](http://www.linkedin.com/in/jan-matthew-pareja-gutierrez-45914b291)
    
    - GitHub:
    
    [JanMatthew - Overview](https://github.com/JanMatthew)
