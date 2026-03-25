# Troubleshooting Guide for HF Azure ML Models

When deploying Hugging Face (HF) models on Azure Machine Learning (Azure ML), you might encounter various issues. This guide aims to help you troubleshoot some common problems.

## 1. ResourceNotReady Error During Azure ML Model Deployment

### Symptom:
You've deployed your model on Azure ML, but the deployment fails, and you encounter a `ResourceNotReady` error.

### Potential Cause:
This error typically occurs when the container initialization takes longer than expected. Azure ML has liveness probes that check the health of the deployment. If the container doesn't initialize within the expected timeframe, the liveness probe fails, leading to a `ResourceNotReady` error.

### Solution:

#### Step 1: Check Deployment Logs
1. Navigate to the Azure ML studio.
2. Go to the **Endpoints** section.
3. Select the endpoint you created.
4. Click on the **Logs** tab.
5. Choose **Online Deployment Log**.

   Look for a message similar to:

   > "You may have hit a ResourceNotReady error for liveness probe. This happens when container initialization is taking too long."

   For reference, see the example log message in the image below.

   ![Azure ML Deployment ResourceNotReady Error](../../assets/aml_deployment_resource_not_ready_error.png)


#### Step 2: Adjust Environment Variable
1. Locate the `.env` file in your project directory.
2. Modify the environment variable `AZURE_ML_MODEL_DEPLOY_LIVENESS_PROBE_INIT_DELAY_SECS` related to liveness probe initial delay time to a value greater than the default. For instance, the default value is 600 seconds, you might change it to 1800 seconds.
3. Save the changes to your `.env` file.

#### Step 3: Redeploy
Redeploy your model by running the deployment script again. This will apply the new settings.

### Additional Resources:
For more detailed troubleshooting steps and explanations, refer to the official Azure ML documentation on troubleshooting online endpoints: [Troubleshoot online endpoints](https://learn.microsoft.com/en-us/azure/machine-learning/how-to-troubleshoot-online-endpoints?view=azureml-api-2&tabs=cli#error-resourcenotready).
