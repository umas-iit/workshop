from __future__ import print_function
from __future__ import unicode_literals

import time
import sys
import os
import shutil
import csv

import pyspark
from pyspark.sql import SparkSession
from pyspark.ml import Pipeline
from pyspark.sql.types import StructField, StructType, StringType, IntegerType, DateType
from pyspark.sql.functions import *
from pyspark.ml.feature import HashingTF, IDF, Tokenizer
from pyspark.ml.linalg import DenseVector
from pyspark.sql.functions import split
from pyspark.sql.functions import udf, col
from pyspark.sql.types import ArrayType, DoubleType
from pyspark.ml.feature import PCA, StandardScaler

def to_array(col):
    def to_array_internal(v):
        if v:
            return v.toArray().tolist()
        else:
            print('EmptyV: {}'.format(v))
            return []
    return udf(to_array_internal, ArrayType(DoubleType())).asNondeterministic()(col)

def main():
    spark = SparkSession.builder.appName('AmazonReviewsSparkProcessor').getOrCreate()
    
    # Convert command line args into a map of args
    args_iter = iter(sys.argv[1:])
    args = dict(zip(args_iter, args_iter))
    
    # Retrieve the args and replace 's3://' with 's3a://' (used by Spark)
    s3_input_data = args['s3_input_data'].replace('s3://', 's3a://')
    print(s3_input_data)
    
    s3_output_data = args['s3_output_data'].replace('s3://', 's3a://')
    print(s3_output_data)
    
    schema = StructType([
        StructField('is_positive_sentiment', IntegerType(), True),
        StructField('marketplace', StringType(), True),
        StructField('customer_id', StringType(), True),
        StructField('review_id', StringType(), True),
        StructField('product_id', StringType(), True),
        StructField('product_parent', StringType(), True),
        StructField('product_title', StringType(), True),
        StructField('product_category', StringType(), True),
        StructField('star_rating', IntegerType(), True),
        StructField('helpful_votes', IntegerType(), True),
        StructField('total_votes', IntegerType(), True),
        StructField('vine', StringType(), True),
        StructField('verified_purchase', StringType(), True),
        StructField('review_headline', StringType(), True),
        StructField('review_body', StringType(), True),
        StructField('review_date', StringType(), True)
    ])
    
    df_csv = spark.read.csv(path=s3_input_data,
                            schema=schema,
                            header=True,
                            quote=None)
    df_csv.show()

    # This dataset should already be clean, but always good to double-check
    print('Showing null review_body rows...')
    df_csv.where(col('review_body').isNull()).show()

    df_csv_cleaned = df_csv.na.drop(subset=['review_body'])
    df_csv_cleaned.where(col('review_body').isNull()).show()
   
    tokenizer = Tokenizer(inputCol='review_body', outputCol='words')
    wordsData = tokenizer.transform(df_csv_cleaned)
    
    hashingTF = HashingTF(inputCol='words', outputCol='raw_features', numFeatures=1000)
    featurizedData = hashingTF.transform(wordsData)
    
    # While applying HashingTF only needs a single pass to the data, applying IDF needs two passes:
    # 1) compute the IDF vector 
    # 2) scale the term frequencies by IDF
    # Therefore, we cache the result of the HashingTF transformation above to speed up the 2nd pass
    featurizedData.cache()

    # spark.mllib's IDF implementation provides an option for ignoring terms
    # which occur in less than a minimum number of documents.
    # In such cases, the IDF for these terms is set to 0.
    # This feature can be used by passing the minDocFreq value to the IDF constructor.
    idf = IDF(inputCol='raw_features', outputCol='features') #, minDocFreq=2)
    idfModel = idf.fit(featurizedData)
    features_df = idfModel.transform(featurizedData)
    features_df.select('is_positive_sentiment', 'features').show()

    # TODO:  Use SVD instead
    # features_vector_rdd = features_df.select('features').rdd.map( lambda row: Vectors.fromML(row.getAs[MLVector]('features') )
    # features_vector_rdd.cache()
    # mat = RowMatrix(features_vector_rdd)
    # k = 300
    # svd = mat.computeSVD(k, computeU=True)
    # TODO:  Reconstruct

    num_features=300
    pca = PCA(k=num_features, inputCol='features', outputCol='pca_features')
    pca_model = pca.fit(features_df)
    pca_features_df = pca_model.transform(features_df).select('is_positive_sentiment', 'pca_features')
    pca_features_df.show(truncate=False)

    standard_scaler = StandardScaler(inputCol='pca_features', outputCol='scaled_pca_features')
    standard_scaler_model = standard_scaler.fit(pca_features_df)
    standard_scaler_features_df = standard_scaler_model.transform(pca_features_df).select('is_positive_sentiment', 'scaled_pca_features')
    standard_scaler_features_df.show(truncate=False)

    expanded_features_df = (standard_scaler_features_df.withColumn('f', to_array(col('scaled_pca_features')))
        .select(['is_positive_sentiment'] + [col('f')[i] for i in range(num_features)]))
    expanded_features_df.show()

    # Remover overwrite to test for this issue
    #    https://stackoverflow.com/questions/51050591/spark-throws-java-io-ioexception-failed-to-rename-when-saving-part-xxxxx-gz
    expanded_features_df.write.csv(path=s3_output_data,
                       header=None,
                       quote=None) #,
#                       mode='overwrite')

    print('Wrote to output file:  {}'.format(s3_output_data))
        

if __name__ == "__main__":
    main()
