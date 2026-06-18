import 'dotenv/config';
import { InstallGlobalCommands } from './utils.js';

// /create <nama> — buat channel baru di TEXT_CATEGORY
const CREATE_COMMAND = {
  name: 'create',
  description: 'Buat text channel baru',
  options: [
    {
      type: 3,
      name: 'nama',
      description: 'Nama channel (otomatis jadi lowercase slug)',
      required: true,
    },
  ],
  type: 1,
};

// /edit <instruksi> — minta Hermes edit sesuatu
const EDIT_COMMAND = {
  name: 'edit',
  description: 'Minta Hermes agent edit/memperbaiki sesuatu',
  options: [
    {
      type: 3,
      name: 'instruksi',
      description: 'Apa yang harus diedit/diperbaiki',
      required: true,
    },
  ],
  type: 1,
};

// /prompt <prompt> — forward prompt ke Hermes
const PROMPT_COMMAND = {
  name: 'prompt',
  description: 'Kirim prompt ke Hermes agent',
  options: [
    {
      type: 3,
      name: 'prompt',
      description: 'Prompt yang akan diproses Hermes',
      required: true,
    },
  ],
  type: 1,
};

const ALL_COMMANDS = [CREATE_COMMAND, EDIT_COMMAND, PROMPT_COMMAND];

InstallGlobalCommands(process.env.APP_ID, ALL_COMMANDS);
