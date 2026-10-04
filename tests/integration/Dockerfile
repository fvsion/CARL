# A Linux client for the sync service test (test_sync_docker.py): systemd as PID 1, a user "carl"
# whose systemd --user manager runs without a login (lingering), python3, bash, curl. No OpenCode or
# Pi: install.sh only writes their configs.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus dbus-user-session python3 curl iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && find /etc/systemd/system /lib/systemd/system -path '*.wants/*' \
      \( -name '*getty*' -o -name '*udev*' -o -name 'systemd-tmpfiles-setup*' -o -name '*resolved*' \) -delete \
 && (userdel -r ubuntu 2>/dev/null || true) \
 && useradd -m -u 1000 -s /bin/bash carl \
 && mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/carl
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
