import cron from "node-cron";
import { logger } from "../utils/logger.js";

interface CronJob {
  name: string;
  schedule: string;
  task: cron.ScheduledTask;
}

export class CronManager {
  private jobs = new Map<string, CronJob>();

  addJob(name: string, schedule: string, handler: () => Promise<void>): void {
    if (!cron.validate(schedule)) {
      logger.error(`Invalid cron schedule: ${schedule} for job ${name}`);
      return;
    }

    if (this.jobs.has(name)) {
      this.removeJob(name);
    }

    const task = cron.schedule(schedule, async () => {
      logger.info(`Cron job running: ${name}`);
      try {
        await handler();
      } catch (err) {
        logger.error(`Cron job failed: ${name}`, { error: err });
      }
    });

    this.jobs.set(name, { name, schedule, task });
    logger.info(`Cron job registered: ${name} (${schedule})`);
  }

  removeJob(name: string): void {
    const job = this.jobs.get(name);
    if (job) {
      job.task.stop();
      this.jobs.delete(name);
      logger.info(`Cron job removed: ${name}`);
    }
  }

  listJobs(): Array<{ name: string; schedule: string }> {
    return Array.from(this.jobs.values()).map((j) => ({
      name: j.name,
      schedule: j.schedule,
    }));
  }

  stopAll(): void {
    for (const [name, job] of this.jobs) {
      job.task.stop();
      logger.info(`Cron job stopped: ${name}`);
    }
    this.jobs.clear();
  }
}
