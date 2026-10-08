FROM eclipse-mosquitto:2
USER root
RUN mkdir -p /mosquitto/runtime /mosquitto/data /mosquitto/log \
    && chown -R 1883:1883 /mosquitto/runtime /mosquitto/data /mosquitto/log \
    && chmod 0750 /mosquitto/runtime
COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY supervise.sh /usr/local/bin/glass-broker-supervise
COPY healthcheck.sh /usr/local/bin/glass-broker-healthcheck
RUN chmod 0555 /usr/local/bin/glass-broker-supervise /usr/local/bin/glass-broker-healthcheck
USER 1883:1883
ENTRYPOINT ["/usr/local/bin/glass-broker-supervise"]
CMD []
