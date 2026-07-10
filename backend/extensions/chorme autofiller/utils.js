const AutoFillUtils = {
  firstNames: ["John", "Jane", "Alex", "Emily", "Chris", "Katie", "Michael", "Sarah", "David", "Laura", "James", "Emma", "Robert", "Olivia", "Daniel", "Sophia", "Matthew", "Isabella", "Anthony", "Mia", "Joshua", "Charlotte"],
  lastNames: ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"],
  companies: ["Stark Industries", "Acme Corp", "Globex", "Initech", "Umbrella Corp", "Soylent Corp", "Massive Dynamic", "Cyberdyne", "Wayne Enterprises", "Hooli", "Pied Piper"],
  streets: ["Main St", "Oak Ave", "Pine St", "Maple Dr", "Cedar Ln", "Washington St", "Park Blvd", "View Rd", "Lake St", "Hill Blvd"],
  cities: ["New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Seattle"],
  states: ["NY", "CA", "IL", "TX", "AZ", "PA", "TX", "CA", "TX", "CA", "TX", "WA"],
  jobs: ["Software Engineer", "Marketing Director", "Data Analyst", "Product Manager", "CEO", "Consultant", "Designer", "Content Creator", "Sales Executive", "Project Manager"],
  genders: ["Male", "Female"],
  securityAnswers: ["Shadow", "Buddy", "Smith", "Springfield", "Toyota", "Blue", "HighSchool"],
  
  randInt: function(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; },
  
  getFirstName: function() { return this.firstNames[this.randInt(0, this.firstNames.length - 1)]; },
  getLastName: function() { return this.lastNames[this.randInt(0, this.lastNames.length - 1)]; },
  getCompany: function() { return this.companies[this.randInt(0, this.companies.length - 1)]; },
  getJobTitle: function() { return this.jobs[this.randInt(0, this.jobs.length - 1)]; },
  getGender: function() { return this.genders[this.randInt(0, 1)]; },
  getSecurityAnswer: function() { return this.securityAnswers[this.randInt(0, this.securityAnswers.length - 1)]; },
  
  getUsername: function(firstName, lastName) {
      return `${firstName.toLowerCase()}${lastName.toLowerCase()}${this.randInt(1000, 9999)}`;
  },
  
  getPassword: function(length = 12) {
      const uppers = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
      const lowers = "abcdefghijklmnopqrstuvwxyz";
      const nums = "0123456789";
      const syms = "!@#$%^&*()_+~`|}{[]:;?><,./-=";
      const all = uppers + lowers + nums + syms;
      let password = uppers.charAt(this.randInt(0, uppers.length - 1)) +
                     lowers.charAt(this.randInt(0, lowers.length - 1)) +
                     nums.charAt(this.randInt(0, nums.length - 1)) +
                     syms.charAt(this.randInt(0, syms.length - 1));
      for (let i = 4; i < length; i++) password += all.charAt(this.randInt(0, all.length - 1));
      return password.split('').sort(() => 0.5 - Math.random()).join('');
  },
  
  getEmail: function(username, domain) {
      let emailDomain = domain && domain.trim() !== "" ? domain.trim() : `random${this.randInt(100,9999)}.com`;
      if (emailDomain.startsWith('@')) emailDomain = emailDomain.substring(1);
      return `${username}@${emailDomain}`;
  },

  getPhone: function() {
      const area = this.randInt(200, 999);
      const prefix = this.randInt(200, 999);
      const line = this.randInt(1000, 9999);
      return `${area}-${prefix}-${line}`;
  },
  
  getAddress: function() {
      const num = this.randInt(100, 9999);
      const street = this.streets[this.randInt(0, this.streets.length - 1)];
      const cityIdx = this.randInt(0, this.cities.length - 1);
      return {
          street: `${num} ${street}`,
          city: this.cities[cityIdx],
          state: this.states[cityIdx],
          zip: this.randInt(10000, 99999).toString()
      };
  },

  getDOB: function() {
      const year = this.randInt(1970, 2000);
      const month = this.randInt(1, 12);
      const day = this.randInt(1, 28);
      return {
          day: day.toString().padStart(2, '0'),
          month: month.toString().padStart(2, '0'),
          year: year.toString(),
          full: `${year}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`
      };
  },
  
  getWebsite: function(companyName) {
      if (!companyName) return "https://example.com";
      const base = companyName.toLowerCase().replace(/[^a-z0-9]/g, '');
      return `https://www.${base}.com`;
  },
  
  getBio: function(job, company, city) {
      const bios = [
          `Passionate ${job} currently working at ${company}. Native to ${city} and always looking for new opportunities to learn and grow.`,
          `Hi! I'm a dedicated ${job} based out of ${city}. Let's build something amazing.`,
          `Expert ${job} at ${company}. When I'm not working, I'm exploring the coffee shops in ${city}.`,
          `${job} | Tech Enthusiast | Traveler. Helping ${company} reach new heights. Located in ${city}.`
      ];
      return bios[this.randInt(0, bios.length - 1)];
  },

  // Spintax processing engine
  // Handles {word1|word2} formatting, parsing from deepest nested first
  spinText: function(text) {
      if (!text) return "";
      const regex = /{([^{}]*)}/g; 
      let result = text;
      while (regex.test(result)) {
          result = result.replace(regex, (match, contents) => {
              const parts = contents.split('|');
              return parts[this.randInt(0, parts.length - 1)];
          });
      }
      return result;
  },

  generatePersona: function(config = {}) {
      const fn = this.getFirstName();
      const ln = this.getLastName();
      const un = this.getUsername(fn, ln);
      const pwLength = config.passwordLength || 12;
      const addr = this.getAddress();
      const comp = this.getCompany();
      const job = this.getJobTitle();
      const dob = this.getDOB();

      let customBio = "";
      if (config.bioSpintax && config.bioSpintax.trim() !== "") {
          customBio = this.spinText(config.bioSpintax);
      } else {
          customBio = this.getBio(job, comp, addr.city);
      }

      return {
          FirstName: fn,
          LastName: ln,
          Username: un,
          Password: this.getPassword(pwLength),
          Email: this.getEmail(un, config.emailDomain),
          Phone: this.getPhone(),
          Company: comp,
          JobTitle: job,
          Website: this.getWebsite(comp),
          Bio: customBio,
          Street: addr.street,
          City: addr.city,
          State: addr.state,
          Zip: addr.zip,
          DOB_Day: dob.day,
          DOB_Month: dob.month,
          DOB_Year: dob.year,
          DOB_Full: dob.full,
          Gender: this.getGender(),
          Country: "United States",
          SecurityAnswer: this.getSecurityAnswer()
      };
  }
};

window.AutoFillUtils = AutoFillUtils;
