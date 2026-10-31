#!/bin/bash
# Backup PostgreSQL Database for MR Signage
# Usage: ./backup_db.sh

BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_USER=${POSTGRES_USER:-olrac}
DB_NAME=${POSTGRES_DB:-olrac_signage}
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql"

mkdir -p "$BACKUP_DIR"

echo "Starting backup for database: $DB_NAME"
docker exec mr_db pg_dump -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Backup completed successfully: $BACKUP_FILE"
    
    # Optional: Keep only last 7 days of backups
    find "$BACKUP_DIR" -type f -name "*.sql" -mtime +7 -exec rm {} \;
    echo "Cleaned up old backups."
else
    echo "Backup failed!"
    exit 1
fi
