FROM pyrit-deployment-base

COPY .pyrit_conf /opt/copyrit/baseline/.pyrit_conf
COPY internal_initializers/ /opt/copyrit/baseline/internal_initializers/
COPY deployment_profile.py /opt/copyrit/deployment_profile.py
RUN chmod -R a=rX /opt/copyrit
USER vscode
ENTRYPOINT ["/opt/venv/bin/python", "/opt/copyrit/deployment_profile.py", "start"]
