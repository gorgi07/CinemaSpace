FROM node:22-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g firebase-tools@14
WORKDIR /srv
COPY firebase.json firestore.rules firestore.indexes.json ./
# Download Firestore emulator JAR at image-build time, not every container start.
# An unsuccessful download now fails the build explicitly rather than hanging on startup.
RUN firebase setup:emulators:firestore
EXPOSE 8080 4000
CMD ["firebase", "emulators:start", "--only", "firestore", "--project", "cinema-lab-local"]
