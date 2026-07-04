ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

COPY run.sh /run.sh
COPY scripts /opt/scripts
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
